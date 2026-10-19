from __future__ import annotations

import hashlib
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
from time import perf_counter
from typing import Callable, Protocol
from uuid import NAMESPACE_URL, uuid5

from app.application.conversion.uploaded_document import UploadedDocument, ingest_uploaded_document
from app.application.conversion_quality import build_line_quality_issues
from app.application.document_classifier import DocumentClassification, classify_document
from app.application.document_routing_policy import enforce_document_routing, enforce_initial_document_routing
from app.application.document_type_classifier import (
    ACCOUNTS_PAYABLE_REPORT,
    DocumentTypeClassification,
    classify_document_type,
)
from app.application.financial_event_projection import event_amount, event_date
from app.application.models import AnalysisData, BeforeAfterRow, NormalizedTransaction, TransactionRow
from app.application.normalization.transaction_normalizer import normalize_transactions as default_normalize_transactions
from app.application.parsers.service import ParsedDocument, ParsingService
from app.application.pdf_parser import parse_pdf_transactions
from app.application.pdf_representation_classifier import classify_pdf_representation
from app.application.reconciliation import ReconciliationResult
from app.application.reconciliation import reconcile_transactions as default_reconcile_transactions
from app.application.source_regions import SourceRegion

OcrProgressCallback = Callable[[int, int], None]
PdfParser = Callable[..., object]


class ReconciliationFunction(Protocol):
    def __call__(self, transactions: list[NormalizedTransaction]) -> ReconciliationResult: ...


@dataclass(frozen=True)
class OperationalPipelineSummary:
    total_volume: float
    inflow_count: int
    outflow_count: int
    reconciled_entries: int
    unmatched_entries: int


@dataclass(frozen=True)
class ProcessingResult:
    analysis_data: AnalysisData
    document: UploadedDocument
    parsed_document: ParsedDocument
    classification: DocumentClassification
    operational_summary: OperationalPipelineSummary
    top_expenses_rows: list[NormalizedTransaction]
    parse_ms: float
    document_type_classification: DocumentTypeClassification | None = None


class ConversionPipeline:
    def __init__(
        self,
        *,
        parser: ParsingService | None = None,
        normalize_transactions: Callable[[list[NormalizedTransaction]], list[NormalizedTransaction]]
        = default_normalize_transactions,
        reconcile_transactions: ReconciliationFunction = default_reconcile_transactions,
        classifier: Callable[..., DocumentClassification] = classify_document,
        document_type_classifier: Callable[..., DocumentTypeClassification] = classify_document_type,
        resolve_opening_balance: Callable[[list[TransactionRow], str | None, str | None], float | None] | None = None,
        is_balance_metadata_row: Callable[[list[TransactionRow], int], bool] | None = None,
        resolve_closing_balance: Callable[[list[TransactionRow], float | None, str | None], float | None] | None = None,
        build_pdf_processing_metrics: Callable[..., dict[str, int | float | str] | None] | None = None,
        resolve_bank_name: Callable[[str, str | None, str | None], str | None] | None = None,
        extract_bank_account_metadata: Callable[[str | None], tuple[str | None, str | None]] | None = None,
        resolve_inferred_bank_code: Callable[[str, str | None, str | None], str | None] | None = None,
        resolve_ofx_account_type: Callable[[str, str, bytes, str | None, str | None], str | None] | None = None,
    ) -> None:
        self.parser = parser or ParsingService()
        self.normalize_transactions = normalize_transactions
        self.reconcile_transactions = reconcile_transactions
        self.classifier = classifier
        self.document_type_classifier = document_type_classifier
        self.resolve_opening_balance = resolve_opening_balance or _default_resolve_opening_balance
        self.is_balance_metadata_row = is_balance_metadata_row or _default_is_balance_metadata_row
        self.resolve_closing_balance = resolve_closing_balance or _default_resolve_closing_balance
        self.build_pdf_processing_metrics = build_pdf_processing_metrics or _default_build_pdf_processing_metrics
        self.resolve_bank_name = resolve_bank_name or _default_resolve_bank_name
        self.extract_bank_account_metadata = extract_bank_account_metadata or _default_extract_bank_account_metadata
        self.resolve_inferred_bank_code = resolve_inferred_bank_code or _default_resolve_inferred_bank_code
        self.resolve_ofx_account_type = resolve_ofx_account_type or _default_resolve_ofx_account_type

    def run(
        self,
        *,
        filename: str,
        raw_bytes: bytes,
        analysis_id: str,
        on_ocr_progress: OcrProgressCallback | None = None,
        max_ocr_pages: int | None = None,
        pdf_parser: PdfParser = parse_pdf_transactions,
    ) -> ProcessingResult:
        document = ingest_uploaded_document(filename=filename, raw_bytes=raw_bytes)
        return self.run_document(
            document=document,
            analysis_id=analysis_id,
            on_ocr_progress=on_ocr_progress,
            max_ocr_pages=max_ocr_pages,
            pdf_parser=pdf_parser,
        )

    def run_document(
        self,
        *,
        document: UploadedDocument,
        analysis_id: str,
        on_ocr_progress: OcrProgressCallback | None = None,
        max_ocr_pages: int | None = None,
        pdf_parser: PdfParser = parse_pdf_transactions,
    ) -> ProcessingResult:
        initial_document_type = self.document_type_classifier(
            filename=document.filename,
            raw_bytes=document.raw_bytes,
        )
        enforce_initial_document_routing(initial_document_type, filename=document.filename, raw_bytes=document.raw_bytes)
        parse_start = perf_counter()
        try:
            parsed_document = self.parser.parse(
                document,
                on_ocr_progress=on_ocr_progress,
                max_ocr_pages=max_ocr_pages,
                pdf_parser=pdf_parser,
            )
        except Exception as exc:
            setattr(exc, "_document_type_classification", initial_document_type)
            raise
        parse_ms = round((perf_counter() - parse_start) * 1000, 3)
        return self.run_parsed_document(
            document=document,
            parsed_document=parsed_document,
            analysis_id=analysis_id,
            parse_ms=parse_ms,
            initial_document_type_classification=initial_document_type,
        )

    def run_parsed_document(
        self,
        *,
        document: UploadedDocument,
        parsed_document: ParsedDocument,
        analysis_id: str,
        parse_ms: float,
        initial_document_type_classification: DocumentTypeClassification | None = None,
    ) -> ProcessingResult:
        total_start = perf_counter()
        filename = document.filename
        raw_bytes = document.raw_bytes
        extension = document.file_type

        if parsed_document.financial_document is not None:
            return self._run_financial_document(document, parsed_document, analysis_id, parse_ms)

        parsed_transactions = parsed_document.transactions
        layout_inference_name = parsed_document.layout_inference_name
        layout_inference_confidence = parsed_document.layout_inference_confidence
        extracted_text = parsed_document.extracted_text
        parse_metrics = parsed_document.parse_metrics
        transaction_warning_types = parsed_document.warning_types or [[] for _ in parsed_transactions]
        transaction_running_balances = parsed_document.running_balances or [None for _ in parsed_transactions]
        canonical_transactions = list(parsed_document.canonical_transactions or [])
        # Physical provenance survives parser-specific accounting reordering.
        # Never sort the accounting rows themselves to serve the reviewer.
        def source_key(index: int) -> tuple[int, int, int]:
            row = canonical_transactions[index] if index < len(canonical_transactions) else None
            sequence = row.source_sequence if row and row.source_sequence is not None else index + 1
            return (row.source_page or 1, row.source_line or sequence, sequence) if row else (1, sequence, sequence)

        source_sequences = {
            index: sequence for sequence, index in enumerate(sorted(range(len(parsed_transactions)), key=source_key), start=1)
        }

        document_type_classification = self.document_type_classifier(
            filename=filename,
            raw_bytes=raw_bytes,
            extracted_text=extracted_text,
            layout_inference_name=layout_inference_name,
            layout_inference_confidence=layout_inference_confidence,
        )
        if (
            document_type_classification.document_type == "unknown"
            and initial_document_type_classification is not None
            and initial_document_type_classification.document_type != "unknown"
        ):
            document_type_classification = initial_document_type_classification
        enforce_document_routing(document_type_classification)
        is_payables_report = document_type_classification.document_type == ACCOUNTS_PAYABLE_REPORT

        classify_start = perf_counter()
        classification_result = self.classifier(
            filename=filename,
            raw_bytes=raw_bytes,
            extracted_text=extracted_text,
            layout_inference_name=layout_inference_name,
            layout_inference_confidence=layout_inference_confidence,
        )
        classify_ms = round((perf_counter() - classify_start) * 1000, 3)

        normalize_start = perf_counter()
        normalized_transactions = self.normalize_transactions(parsed_transactions)
        if is_payables_report or layout_inference_name == "bradesco_extrato_cartao_web_usd_brl_v1":
            normalized_transactions = [replace(normalized, description=original.description)
                                       for normalized, original in zip(normalized_transactions, parsed_transactions, strict=True)]
        normalize_ms = round((perf_counter() - normalize_start) * 1000, 3)

        metadata_candidate_rows = _build_transaction_rows(
            transactions=normalized_transactions,
            statuses=["unmatched" for _ in normalized_transactions],
            warning_types=transaction_warning_types,
            running_balances=transaction_running_balances,
        )
        opening_balance = self.resolve_opening_balance(
            metadata_candidate_rows,
            extracted_text,
            layout_inference_name,
        )
        if is_payables_report or (parse_metrics and parse_metrics.get("opening_balance_ambiguous")):
            opening_balance = None
        kept_indices = [
            idx
            for idx, _row in enumerate(metadata_candidate_rows)
            if is_payables_report or not self.is_balance_metadata_row(metadata_candidate_rows, idx)
        ]

        parsed_transactions = [parsed_transactions[idx] for idx in kept_indices]
        transactions = [normalized_transactions[idx] for idx in kept_indices]
        transaction_warning_types = [
            transaction_warning_types[idx] if idx < len(transaction_warning_types) else []
            for idx in kept_indices
        ]
        transaction_running_balances = [
            transaction_running_balances[idx] if idx < len(transaction_running_balances) else None
            for idx in kept_indices
        ]
        selected_canonical_transactions = [
            canonical_transactions[idx]
            for idx in kept_indices
            if idx < len(canonical_transactions)
        ]

        reconcile_start = perf_counter()
        reconciliation_result = self.reconcile_transactions(transactions)
        reconcile_ms = round((perf_counter() - reconcile_start) * 1000, 3)

        preview_before_after = [
            BeforeAfterRow(
                date=after_item.date,
                description_before=before_item.description,
                description_after=after_item.description,
                amount_before=before_item.amount,
                amount_after=after_item.amount,
            )
            for before_item, after_item in zip(parsed_transactions[:20], transactions[:20], strict=False)
        ]
        preview_rows = _build_transaction_rows(
            transactions=transactions,
            statuses=reconciliation_result.statuses,
            warning_types=transaction_warning_types,
            running_balances=transaction_running_balances,
            source_pages=[canonical_transactions[idx].source_page if idx < len(canonical_transactions) else None
                          for idx in kept_indices],
            source_lines=[canonical_transactions[idx].source_line if idx < len(canonical_transactions) else None
                          for idx in kept_indices],
            source_sequences=[source_sequences[idx] for idx in kept_indices],
            source_regions=[canonical_transactions[idx].source_regions if idx < len(canonical_transactions) else []
                            for idx in kept_indices],
            analysis_id=analysis_id,
        )
        report_rows = _build_transaction_rows(
            transactions=transactions,
            statuses=reconciliation_result.statuses,
            warning_types=transaction_warning_types,
            running_balances=transaction_running_balances,
            source_pages=[canonical_transactions[idx].source_page if idx < len(canonical_transactions) else None
                          for idx in kept_indices],
            source_lines=[canonical_transactions[idx].source_line if idx < len(canonical_transactions) else None
                          for idx in kept_indices],
            source_sequences=[source_sequences[idx] for idx in kept_indices],
            source_regions=[canonical_transactions[idx].source_regions if idx < len(canonical_transactions) else []
                            for idx in kept_indices],
            analysis_id=analysis_id,
        )

        total_inflows = round(sum(item.amount for item in transactions if item.amount > 0), 2)
        total_outflows = round(sum(item.amount for item in transactions if item.amount < 0), 2)
        net_total = round(total_inflows + total_outflows, 2)
        closing_balance = self.resolve_closing_balance(
            preview_rows,
            opening_balance,
            layout_inference_name,
        )
        bank_name = parsed_document.bank_name or self.resolve_bank_name(
            extension, layout_inference_name, extracted_text
        )
        bank_branch, account_number = self.extract_bank_account_metadata(extracted_text)
        bank_branch = parsed_document.bank_branch or bank_branch
        account_number = parsed_document.account_number or account_number
        inferred_bank_code = parsed_document.bank_code or self.resolve_inferred_bank_code(
            extension, layout_inference_name, bank_name
        )
        if is_payables_report:
            opening_balance = closing_balance = None
            bank_name = bank_branch = account_number = inferred_bank_code = None
        total_volume = round(sum(abs(item.amount) for item in transactions), 2)
        inflow_count = sum(1 for item in transactions if item.amount > 0)
        outflow_count = sum(1 for item in transactions if item.amount < 0)
        reconciled_entries = sum(1 for status in reconciliation_result.statuses if status != "unmatched")
        unmatched_entries = len(transactions) - reconciled_entries
        top_expenses_rows = sorted((item for item in transactions if item.amount < 0), key=lambda x: x.amount)[:10]
        pdf_processing_metrics = self.build_pdf_processing_metrics(
            extension=extension,
            parse_metrics=parse_metrics,
            parse_ms=parse_ms,
            classify_ms=classify_ms,
            normalize_ms=normalize_ms,
            reconcile_ms=reconcile_ms,
            total_ms=round(parse_ms + ((perf_counter() - total_start) * 1000), 3),
        )
        ofx_account_type = self.resolve_ofx_account_type(
            extension,
            filename,
            raw_bytes,
            extracted_text,
            layout_inference_name,
        )
        if is_payables_report:
            ofx_account_type = None

        analysis_data = AnalysisData(
            analysis_id=analysis_id,
            file_type=extension,
            upload_filename=filename or None,
            semantic_type=classification_result.semantic_type,
            semantic_confidence=classification_result.confidence,
            semantic_evidence=list(classification_result.evidence or []),
            document_type=document_type_classification.document_type,
            document_type_confidence=document_type_classification.confidence,
            document_type_evidence=list(document_type_classification.evidence or []),
            document_classification_version=document_type_classification.classifier_version,
            transactions_total=len(transactions),
            total_inflows=total_inflows,
            total_outflows=total_outflows,
            net_total=net_total,
            preview_transactions=preview_rows,
            report_transactions=report_rows,
            preview_before_after=preview_before_after,
            matched_groups=reconciliation_result.matched_groups,
            reversed_entries=reconciliation_result.reversed_entries,
            potential_duplicates=reconciliation_result.potential_duplicates,
            updated_at=datetime.now(timezone.utc).isoformat(),
            layout_inference_name=layout_inference_name,
            layout_inference_confidence=layout_inference_confidence,
            pdf_processing_metrics=pdf_processing_metrics,
            quality_issues=build_line_quality_issues(selected_canonical_transactions),
            ofx_account_type=ofx_account_type,
            opening_balance=opening_balance,
            closing_balance=closing_balance,
            bank_name=bank_name,
            bank_branch=bank_branch,
            account_number=account_number,
            bank_code=inferred_bank_code,
            document_parts=list(parsed_document.document_parts or []),
        )

        return ProcessingResult(
            analysis_data=analysis_data,
            document=document,
            parsed_document=parsed_document,
            classification=classification_result,
            document_type_classification=document_type_classification,
            operational_summary=OperationalPipelineSummary(
                total_volume=total_volume,
                inflow_count=inflow_count,
                outflow_count=outflow_count,
                reconciled_entries=reconciled_entries,
                unmatched_entries=unmatched_entries,
            ),
            top_expenses_rows=top_expenses_rows,
            parse_ms=parse_ms,
        )


    def _run_financial_document(self, document, parsed, analysis_id, parse_ms) -> ProcessingResult:
        financial = deepcopy(parsed.financial_document)
        digest = hashlib.sha256(document.raw_bytes).hexdigest()
        financial["source_document_sha256"] = digest
        classification = self.document_type_classifier(document.filename, document.raw_bytes, extracted_text=parsed.extracted_text)
        financial["classification"] = {"document_type": classification.document_type, "confidence": classification.confidence,
                                       "evidence": classification.evidence, "version": classification.classifier_version}
        financial["representation"] = classify_pdf_representation(document.raw_bytes).representation
        semantic = DocumentClassification(semantic_type=financial["document_type"], confidence=classification.confidence,
                                          evidence=list(classification.evidence))
        rows = []
        for index, event in enumerate(financial["events"]):
            event["event_id"] = f"{digest[:16]}_{event['event_id']}"
            canonical = (parsed.canonical_transactions or [])[index]
            rows.append(TransactionRow(
                date=event_date(event), description=event["description"], amount=event_amount(event),
                category=event["event_type"], reconciliation_status="not_applicable" if event["cash_effect"] is None else "unmatched",
                source_page=canonical.source_page, source_line=canonical.source_line, source_regions=canonical.source_regions,
                source_sequence=index + 1, transaction_id=event["event_id"],
            ))
        cash = [Decimal(e["cash_effect"]) for e in financial["events"] if e["cash_effect"] is not None]
        inflows = sum((value for value in cash if value > 0), Decimal("0"))
        outflows = sum((value for value in cash if value < 0), Decimal("0"))
        metrics = self.build_pdf_processing_metrics(extension="pdf", parse_metrics=parsed.parse_metrics,
            parse_ms=parse_ms, classify_ms=0, normalize_ms=0, reconcile_ms=0, total_ms=parse_ms)
        data = AnalysisData(
            analysis_id=analysis_id, file_type=document.file_type, upload_filename=document.filename,
            transactions_total=len(rows), total_inflows=float(inflows), total_outflows=float(outflows), net_total=float(inflows + outflows),
            preview_transactions=rows, report_transactions=rows, semantic_type=semantic.semantic_type,
            semantic_confidence=semantic.confidence, semantic_evidence=semantic.evidence,
            document_type=classification.document_type, document_type_confidence=classification.confidence,
            document_type_evidence=classification.evidence, document_classification_version=classification.classifier_version,
            layout_inference_name=parsed.layout_inference_name, layout_inference_confidence=parsed.layout_inference_confidence,
            pdf_processing_metrics=metrics, financial_document=financial, document_parts=list(parsed.document_parts or []),
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        return ProcessingResult(analysis_data=data, document=document, parsed_document=parsed, classification=semantic,
            document_type_classification=classification, parse_ms=parse_ms, top_expenses_rows=[],
            operational_summary=OperationalPipelineSummary(total_volume=float(sum(map(abs, cash), Decimal("0"))),
                inflow_count=sum(value > 0 for value in cash), outflow_count=sum(value < 0 for value in cash),
                reconciled_entries=0, unmatched_entries=len(cash)))


def _build_transaction_rows(
    *,
    transactions: list[NormalizedTransaction],
    statuses: list[str],
    warning_types: list[list[str]],
    running_balances: list[float | None],
    source_pages: list[int | None] | None = None,
    source_lines: list[int | None] | None = None,
    source_sequences: list[int] | None = None,
    source_regions: list[list[SourceRegion]] | None = None,
    analysis_id: str | None = None,
) -> list[TransactionRow]:
    return [
        TransactionRow(
            date=item.date,
            description=item.description,
            amount=item.amount,
            category="Outros",
            reconciliation_status=statuses[idx],
            running_balance=running_balances[idx] if idx < len(running_balances) else None,
            warning_types=warning_types[idx] if idx < len(warning_types) else [],
            source_page=source_pages[idx] if source_pages and idx < len(source_pages) else None,
            source_line=source_lines[idx] if source_lines else None,
            source_sequence=source_sequences[idx] if source_sequences else None,
            source_regions=list(source_regions[idx]) if source_regions else [],
            transaction_id=(f"txn_{uuid5(NAMESPACE_URL, f'{analysis_id}:{source_sequences[idx]}').hex}"
                            if source_sequences else None),
        )
        for idx, item in enumerate(transactions)
    ]


def _default_resolve_opening_balance(
    _rows: list[TransactionRow],
    _extracted_text: str | None,
    _layout_inference_name: str | None,
) -> float | None:
    return None


def _default_is_balance_metadata_row(_rows: list[TransactionRow], _index: int) -> bool:
    return False


def _default_resolve_closing_balance(
    _rows: list[TransactionRow],
    _opening_balance: float | None,
    _layout_inference_name: str | None,
) -> float | None:
    return None


def _default_build_pdf_processing_metrics(**_kwargs) -> dict[str, int | float | str] | None:
    return None


def _default_resolve_bank_name(
    _extension: str,
    _layout_inference_name: str | None,
    _extracted_text: str | None,
) -> str | None:
    return None


def _default_extract_bank_account_metadata(_extracted_text: str | None) -> tuple[str | None, str | None]:
    return None, None


def _default_resolve_inferred_bank_code(
    _extension: str,
    _layout_inference_name: str | None,
    _bank_name: str | None,
) -> str | None:
    return None


def _default_resolve_ofx_account_type(
    _extension: str,
    _filename: str,
    _raw_bytes: bytes,
    _extracted_text: str | None,
    _layout_inference_name: str | None,
) -> str | None:
    return None


# Compatibility name for existing callers; wire payloads are unchanged.
ConversionPipelineResult = ProcessingResult
