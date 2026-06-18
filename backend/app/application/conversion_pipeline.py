from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from time import perf_counter
from typing import Callable, Protocol

from app.application.document_classifier import DocumentClassification, classify_document
from app.application.ingestion import IngestedDocument, ingest_uploaded_document
from app.application.models import AnalysisData, BeforeAfterRow, NormalizedTransaction, TransactionRow
from app.application.normalization.transaction_normalizer import normalize_transactions as default_normalize_transactions
from app.application.parsers.service import ParsedDocument, ParsingService
from app.application.pdf_parser import parse_pdf_transactions
from app.application.reconciliation import ReconciliationResult
from app.application.reconciliation import reconcile_transactions as default_reconcile_transactions

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
class ConversionPipelineResult:
    analysis_data: AnalysisData
    document: IngestedDocument
    parsed_document: ParsedDocument
    classification: DocumentClassification
    operational_summary: OperationalPipelineSummary
    top_expenses_rows: list[NormalizedTransaction]
    parse_ms: float


class ConversionPipeline:
    def __init__(
        self,
        *,
        parser: ParsingService | None = None,
        normalize_transactions: Callable[[list[NormalizedTransaction]], list[NormalizedTransaction]]
        = default_normalize_transactions,
        reconcile_transactions: ReconciliationFunction = default_reconcile_transactions,
        classifier: Callable[..., DocumentClassification] = classify_document,
        resolve_opening_balance: Callable[[list[TransactionRow], str | None], float | None] | None = None,
        is_balance_metadata_row: Callable[[list[TransactionRow], int], bool] | None = None,
        resolve_closing_balance: Callable[[list[TransactionRow], float | None], float | None] | None = None,
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
    ) -> ConversionPipelineResult:
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
        document: IngestedDocument,
        analysis_id: str,
        on_ocr_progress: OcrProgressCallback | None = None,
        max_ocr_pages: int | None = None,
        pdf_parser: PdfParser = parse_pdf_transactions,
    ) -> ConversionPipelineResult:
        total_start = perf_counter()
        filename = document.filename
        raw_bytes = document.raw_bytes
        extension = document.file_type

        parse_start = perf_counter()
        parsed_document = self.parser.parse(
            document,
            on_ocr_progress=on_ocr_progress,
            max_ocr_pages=max_ocr_pages,
            pdf_parser=pdf_parser,
        )
        parse_ms = round((perf_counter() - parse_start) * 1000, 3)

        parsed_transactions = parsed_document.transactions
        layout_inference_name = parsed_document.layout_inference_name
        layout_inference_confidence = parsed_document.layout_inference_confidence
        extracted_text = parsed_document.extracted_text
        parse_metrics = parsed_document.parse_metrics
        transaction_warning_types = parsed_document.warning_types or [[] for _ in parsed_transactions]
        transaction_running_balances = parsed_document.running_balances or [None for _ in parsed_transactions]

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
        normalize_ms = round((perf_counter() - normalize_start) * 1000, 3)

        metadata_candidate_rows = _build_transaction_rows(
            transactions=normalized_transactions,
            statuses=["unmatched" for _ in normalized_transactions],
            warning_types=transaction_warning_types,
            running_balances=transaction_running_balances,
        )
        opening_balance = self.resolve_opening_balance(metadata_candidate_rows, extracted_text)
        kept_indices = [
            idx
            for idx, _row in enumerate(metadata_candidate_rows)
            if not self.is_balance_metadata_row(metadata_candidate_rows, idx)
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
        )
        report_rows = _build_transaction_rows(
            transactions=transactions,
            statuses=reconciliation_result.statuses,
            warning_types=transaction_warning_types,
            running_balances=transaction_running_balances,
        )

        total_inflows = round(sum(item.amount for item in transactions if item.amount > 0), 2)
        total_outflows = round(sum(item.amount for item in transactions if item.amount < 0), 2)
        net_total = round(total_inflows + total_outflows, 2)
        closing_balance = self.resolve_closing_balance(preview_rows, opening_balance)
        bank_name = self.resolve_bank_name(extension, layout_inference_name, extracted_text)
        bank_branch, account_number = self.extract_bank_account_metadata(extracted_text)
        inferred_bank_code = self.resolve_inferred_bank_code(extension, layout_inference_name, bank_name)
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
            total_ms=round((perf_counter() - total_start) * 1000, 3),
        )
        ofx_account_type = self.resolve_ofx_account_type(
            extension,
            filename,
            raw_bytes,
            extracted_text,
            layout_inference_name,
        )

        analysis_data = AnalysisData(
            analysis_id=analysis_id,
            file_type=extension,
            upload_filename=filename or None,
            semantic_type=classification_result.semantic_type,
            semantic_confidence=classification_result.confidence,
            semantic_evidence=list(classification_result.evidence or []),
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
            ofx_account_type=ofx_account_type,
            opening_balance=opening_balance,
            closing_balance=closing_balance,
            bank_name=bank_name,
            bank_branch=bank_branch,
            account_number=account_number,
            bank_code=inferred_bank_code,
        )

        return ConversionPipelineResult(
            analysis_data=analysis_data,
            document=document,
            parsed_document=parsed_document,
            classification=classification_result,
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


def _build_transaction_rows(
    *,
    transactions: list[NormalizedTransaction],
    statuses: list[str],
    warning_types: list[list[str]],
    running_balances: list[float | None],
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
        )
        for idx, item in enumerate(transactions)
    ]


def _default_resolve_opening_balance(_rows: list[TransactionRow], _extracted_text: str | None) -> float | None:
    return None


def _default_is_balance_metadata_row(_rows: list[TransactionRow], _index: int) -> bool:
    return False


def _default_resolve_closing_balance(_rows: list[TransactionRow], _opening_balance: float | None) -> float | None:
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
