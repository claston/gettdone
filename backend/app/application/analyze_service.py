import logging
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Callable
from uuid import uuid4

from app.application.csv_parser import parse_csv_transactions
from app.application.document_classifier import classify_document
from app.application.errors import UnsupportedFileTypeError
from app.application.models import AnalysisData, BeforeAfterRow, NormalizedTransaction, TransactionRow
from app.application.normalizer import normalize_transactions
from app.application.ofx_parser import parse_ofx_transactions
from app.application.pdf_parser import parse_pdf_transactions
from app.application.reconciliation import reconcile_transactions
from app.application.storage_service import TempAnalysisStorage
from app.application.xlsx_parser import parse_xlsx_transactions
from app.schemas import (
    AnalyzeResponse,
    BeforeAfterPreview,
    CategorySummary,
    Insight,
    OperationalSummary,
    ReconciliationSummary,
    TopExpense,
    TransactionPreview,
)

SUPPORTED_EXTENSIONS = {"csv", "xlsx", "ofx", "pdf"}
logger = logging.getLogger(__name__)


class AnalyzeService:
    def __init__(self, storage: TempAnalysisStorage) -> None:
        self.storage = storage

    def analyze(
        self,
        filename: str,
        raw_bytes: bytes,
        on_ocr_progress: Callable[[int, int], None] | None = None,
    ) -> AnalyzeResponse:
        total_start = perf_counter()
        extension = Path(filename).suffix.replace(".", "").lower()
        if extension not in SUPPORTED_EXTENSIONS:
            raise UnsupportedFileTypeError
        logger.info(
            "analyze_start extension=%s size_bytes=%d filename=%s",
            extension,
            len(raw_bytes),
            (filename or "")[:120],
        )

        analysis_id = f"an_{uuid4().hex[:12]}"
        parse_start = perf_counter()
        (
            parsed_transactions,
            layout_inference_name,
            layout_inference_confidence,
            extracted_text,
            parse_metrics,
            transaction_warning_types,
            transaction_running_balances,
        ) = self._build_transactions_for_extension(
            extension,
            raw_bytes,
            on_ocr_progress=on_ocr_progress,
        )
        parse_ms = round((perf_counter() - parse_start) * 1000, 3)
        classify_start = perf_counter()
        classification_result = classify_document(
            filename=filename,
            raw_bytes=raw_bytes,
            extracted_text=extracted_text,
            layout_inference_name=layout_inference_name,
            layout_inference_confidence=layout_inference_confidence,
        )
        classify_ms = round((perf_counter() - classify_start) * 1000, 3)
        normalize_start = perf_counter()
        transactions = normalize_transactions(parsed_transactions)
        normalize_ms = round((perf_counter() - normalize_start) * 1000, 3)
        reconcile_start = perf_counter()
        reconciliation_result = reconcile_transactions(transactions)
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
        preview_rows = [
            TransactionRow(
                date=item.date,
                description=item.description,
                amount=item.amount,
                category="Outros",
                reconciliation_status=reconciliation_result.statuses[idx],
                running_balance=(
                    transaction_running_balances[idx] if idx < len(transaction_running_balances) else None
                ),
                warning_types=transaction_warning_types[idx] if idx < len(transaction_warning_types) else [],
            )
            for idx, item in enumerate(transactions)
        ]
        report_rows = [
            TransactionRow(
                date=item.date,
                description=item.description,
                amount=item.amount,
                category="Outros",
                reconciliation_status=reconciliation_result.statuses[idx],
                running_balance=(
                    transaction_running_balances[idx] if idx < len(transaction_running_balances) else None
                ),
                warning_types=transaction_warning_types[idx] if idx < len(transaction_warning_types) else [],
            )
            for idx, item in enumerate(transactions)
        ]

        total_inflows = round(sum(item.amount for item in transactions if item.amount > 0), 2)
        total_outflows = round(sum(item.amount for item in transactions if item.amount < 0), 2)
        net_total = round(total_inflows + total_outflows, 2)
        total_volume = round(sum(abs(item.amount) for item in transactions), 2)
        inflow_count = sum(1 for item in transactions if item.amount > 0)
        outflow_count = sum(1 for item in transactions if item.amount < 0)
        reconciled_entries = sum(1 for status in reconciliation_result.statuses if status != "unmatched")
        unmatched_entries = len(transactions) - reconciled_entries
        top_expenses_rows = sorted((item for item in transactions if item.amount < 0), key=lambda x: x.amount)[:10]
        pdf_processing_metrics = self._build_pdf_processing_metrics(
            extension=extension,
            parse_metrics=parse_metrics,
            parse_ms=parse_ms,
            classify_ms=classify_ms,
            normalize_ms=normalize_ms,
            reconcile_ms=reconcile_ms,
            total_ms=round((perf_counter() - total_start) * 1000, 3),
        )
        ofx_account_type = self._resolve_ofx_account_type(
            extension=extension,
            filename=filename,
            raw_bytes=raw_bytes,
            extracted_text=extracted_text,
            layout_inference_name=layout_inference_name,
        )

        analysis_data = AnalysisData(
            analysis_id=analysis_id,
            file_type=extension,
            upload_filename=filename or None,
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
        )
        expires_at = self.storage.save_analysis(analysis_data)
        logger.info(
            "analyze_done analysis_id=%s extension=%s total_ms=%.3f parse_ms=%.3f tx_count=%d layout=%s parser=%s",
            analysis_id,
            extension,
            round((perf_counter() - total_start) * 1000, 3),
            parse_ms,
            len(transactions),
            layout_inference_name or "",
            (pdf_processing_metrics or {}).get("selected_parser", ""),
        )

        insights = [
            Insight(
                type=f"{extension}_real_parser",
                title=f"{extension.upper()} processado",
                description=f"Extrato {extension.upper()} processado com parser real e normalizacao inicial.",
            )
        ]
        review_insight = self._build_export_review_insight(
            extension=extension,
            pdf_processing_metrics=pdf_processing_metrics,
        )
        if review_insight is not None:
            insights.append(review_insight)

        return AnalyzeResponse(
            analysis_id=analysis_id,
            file_type=extension,
            semantic_type=classification_result.semantic_type,
            semantic_confidence=classification_result.confidence,
            semantic_evidence=classification_result.evidence,
            transactions_total=analysis_data.transactions_total,
            total_inflows=analysis_data.total_inflows,
            total_outflows=analysis_data.total_outflows,
            net_total=analysis_data.net_total,
            operational_summary=OperationalSummary(
                total_volume=total_volume,
                inflow_count=inflow_count,
                outflow_count=outflow_count,
                reconciled_entries=reconciled_entries,
                unmatched_entries=unmatched_entries,
            ),
            reconciliation=ReconciliationSummary(
                matched_groups=analysis_data.matched_groups,
                reversed_entries=analysis_data.reversed_entries,
                potential_duplicates=analysis_data.potential_duplicates,
            ),
            categories=[CategorySummary(category="Outros", total=net_total, count=len(transactions))],
            top_expenses=[
                TopExpense(
                    description=row.description,
                    amount=row.amount,
                    date=row.date,
                    category="Outros",
                )
                for row in top_expenses_rows
            ],
            insights=insights,
            preview_transactions=[
                TransactionPreview(
                    date=row.date,
                    description=row.description,
                    amount=row.amount,
                    running_balance=row.running_balance,
                    category=row.category,
                    reconciliation_status=row.reconciliation_status,
                    warning_types=list(row.warning_types or []),
                )
                for row in preview_rows
            ],
            preview_before_after=[
                BeforeAfterPreview(
                    date=row.date,
                    description_before=row.description_before,
                    description_after=row.description_after,
                    amount_before=row.amount_before,
                    amount_after=row.amount_after,
                )
                for row in preview_before_after
            ],
            expires_at=expires_at,
            updated_at=analysis_data.updated_at,
            layout_inference_name=layout_inference_name,
            layout_inference_confidence=layout_inference_confidence,
            pdf_processing_metrics=pdf_processing_metrics,
        )

    def _build_transactions_for_extension(
        self,
        extension: str,
        raw_bytes: bytes,
        on_ocr_progress: Callable[[int, int], None] | None = None,
    ) -> tuple[
        list[NormalizedTransaction],
        str | None,
        float | None,
        str | None,
        dict[str, int | float | str] | None,
        list[list[str]],
        list[float | None],
    ]:
        if extension == "csv":
            transactions = parse_csv_transactions(raw_bytes)
            return transactions, None, None, None, None, [[] for _ in transactions], [None for _ in transactions]
        if extension == "xlsx":
            transactions = parse_xlsx_transactions(raw_bytes)
            return transactions, None, None, None, None, [[] for _ in transactions], [None for _ in transactions]
        if extension == "ofx":
            transactions = parse_ofx_transactions(raw_bytes)
            return transactions, None, None, None, None, [[] for _ in transactions], [None for _ in transactions]
        if on_ocr_progress is None:
            result = parse_pdf_transactions(raw_bytes)
        else:
            result = parse_pdf_transactions(raw_bytes, on_ocr_progress=on_ocr_progress)
        warning_types = [
            list(item.warnings or [])
            for item in (result.canonical_transactions or [])
        ]
        running_balances = [
            item.running_balance
            for item in (result.canonical_transactions or [])
        ]
        if len(warning_types) < len(result.transactions):
            warning_types.extend([[] for _ in range(len(result.transactions) - len(warning_types))])
        if len(running_balances) < len(result.transactions):
            running_balances.extend([None for _ in range(len(result.transactions) - len(running_balances))])
        return (
            result.transactions,
            result.layout.layout_name,
            result.layout.confidence,
            result.extracted_text,
            result.parse_metrics,
            warning_types,
            running_balances,
        )

    def _build_pdf_processing_metrics(
        self,
        *,
        extension: str,
        parse_metrics: dict[str, int | float | str] | None,
        parse_ms: float,
        classify_ms: float,
        normalize_ms: float,
        reconcile_ms: float,
        total_ms: float,
    ) -> dict[str, int | float | str] | None:
        if extension != "pdf" or parse_metrics is None:
            return None

        return {
            "total_ms": total_ms,
            "parse_ms": parse_ms,
            "classify_ms": classify_ms,
            "normalize_ms": normalize_ms,
            "reconcile_ms": reconcile_ms,
            "page_count": int(parse_metrics.get("page_count", 0)),
            "extracted_char_count": int(parse_metrics.get("extracted_char_count", 0)),
            "flattened_line_count": int(parse_metrics.get("flattened_line_count", 0)),
            "grouped_transactions_count": int(parse_metrics.get("grouped_transactions_count", 0)),
            "inline_candidates_count": int(parse_metrics.get("inline_candidates_count", 0)),
            "inline_transactions_count": int(parse_metrics.get("inline_transactions_count", 0)),
            "tabular_candidates_count": int(parse_metrics.get("tabular_candidates_count", 0)),
            "tabular_transactions_count": int(parse_metrics.get("tabular_transactions_count", 0)),
            "columnar_candidates_count": int(parse_metrics.get("columnar_candidates_count", 0)),
            "columnar_transactions_count": int(parse_metrics.get("columnar_transactions_count", 0)),
            "selected_parser": str(parse_metrics.get("selected_parser", "unknown")),
            "parser_selection_reason": str(parse_metrics.get("parser_selection_reason", "")),
            "inline_decision": str(parse_metrics.get("inline_decision", "")),
            "tabular_decision": str(parse_metrics.get("tabular_decision", "")),
            "columnar_decision": str(parse_metrics.get("columnar_decision", "")),
            "confidence_band": str(parse_metrics.get("confidence_band", "")),
            "export_recommendation": str(parse_metrics.get("export_recommendation", "")),
            "export_recommendation_reason": str(parse_metrics.get("export_recommendation_reason", "")),
            "balance_consistency_checked": int(parse_metrics.get("balance_consistency_checked", 0)),
            "balance_consistency_failed": int(parse_metrics.get("balance_consistency_failed", 0)),
            "canonical_transactions_count": int(parse_metrics.get("canonical_transactions_count", 0)),
            "canonical_with_running_balance_count": int(parse_metrics.get("canonical_with_running_balance_count", 0)),
            "canonical_with_external_reference_count": int(
                parse_metrics.get("canonical_with_external_reference_count", 0)
            ),
            "canonical_warning_count": int(parse_metrics.get("canonical_warning_count", 0)),
            "canonical_balance_warning_count": int(parse_metrics.get("canonical_balance_warning_count", 0)),
            "canonical_warning_transactions_count": int(parse_metrics.get("canonical_warning_transactions_count", 0)),
            "canonical_warning_types_count": int(parse_metrics.get("canonical_warning_types_count", 0)),
            "canonical_warning_types": str(parse_metrics.get("canonical_warning_types", "")),
            "canonical_warning_types_list": [
                item
                for item in str(parse_metrics.get("canonical_warning_types_list", "")).split("|")
                if item.strip()
            ],
            "canonical_running_balance_coverage_rate": float(
                parse_metrics.get("canonical_running_balance_coverage_rate", 0.0)
            ),
            "canonical_external_reference_coverage_rate": float(
                parse_metrics.get("canonical_external_reference_coverage_rate", 0.0)
            ),
            "canonical_warning_transaction_rate": float(parse_metrics.get("canonical_warning_transaction_rate", 0.0)),
            "canonical_source_parser_grouped_count": int(parse_metrics.get("canonical_source_parser_grouped_count", 0)),
            "canonical_source_parser_inline_count": int(parse_metrics.get("canonical_source_parser_inline_count", 0)),
            "canonical_source_parser_tabular_count": int(parse_metrics.get("canonical_source_parser_tabular_count", 0)),
            "canonical_source_parser_columnar_count": int(
                parse_metrics.get("canonical_source_parser_columnar_count", 0)
            ),
            "canonical_source_parser_types_count": int(parse_metrics.get("canonical_source_parser_types_count", 0)),
            "canonical_source_parser_types": str(parse_metrics.get("canonical_source_parser_types", "")),
            "canonical_source_parser_types_list": [
                item
                for item in str(parse_metrics.get("canonical_source_parser_types_list", "")).split("|")
                if item.strip()
            ],
        }

    def _resolve_ofx_account_type(
        self,
        *,
        extension: str,
        filename: str,
        raw_bytes: bytes,
        extracted_text: str | None,
        layout_inference_name: str | None,
    ) -> str | None:
        if extension == "ofx":
            decoded = self._decode_optional_text(raw_bytes)
            normalized = decoded.upper()
            if "CREDITCARDMSGSRSV1" in normalized or "<CCSTMTRS>" in normalized:
                return "credit_card"
            if "BANKMSGSRSV1" in normalized or "<STMTRS>" in normalized:
                return "bank"
            return None

        if extension == "pdf":
            normalized = self._normalize_text_for_profile((extracted_text or "") + " " + filename)
            layout_name = str(layout_inference_name or "").strip().lower()

            bank_indicators = (
                "TRANSFERENCIA RECEBIDA",
                "TRANSFERENCIA ENVIADA",
                "TOTAL DE ENTRADAS",
                "TOTAL DE SAIDAS",
                "SALDO DO DIA",
                "EXTRATO CONTA",
            )
            has_bank_indicators = any(token in normalized for token in bank_indicators)
            if layout_name == "nubank_statement_ptbr" and has_bank_indicators:
                return "bank"

            card_indicators = (
                "FATURA",
                "TOTAL A PAGAR",
                "TOTAL DE COMPRAS DE TODOS OS CARTOES",
                "PAGAMENTOS E FINANCIAMENTOS",
                "CARTAO DE CREDITO",
                "DATA DE VENCIMENTO",
            )
            card_matches = sum(1 for token in card_indicators if token in normalized)
            has_card_window = "TRANSACOES DE" in normalized and " A " in normalized

            if card_matches >= 2 and (has_card_window or "TOTAL A PAGAR" in normalized) and not has_bank_indicators:
                return "credit_card"
            return None

        return None

    def _build_export_review_insight(
        self,
        *,
        extension: str,
        pdf_processing_metrics: dict[str, int | float | str] | None,
    ) -> Insight | None:
        if extension != "pdf" or pdf_processing_metrics is None:
            return None
        recommendation = str(pdf_processing_metrics.get("export_recommendation", "")).strip().lower()
        if recommendation != "review_recommended":
            return None
        reason = str(pdf_processing_metrics.get("export_recommendation_reason", "")).strip()
        reason_suffix = f" ({reason})" if reason else ""
        return Insight(
            type="pdf_export_review_recommended",
            title="Revisao manual recomendada",
            description=(
                "A exportacao permanece disponivel, mas recomendamos revisar as transacoes antes de concluir"
                f"{reason_suffix}."
            ),
        )

    def _decode_optional_text(self, raw_bytes: bytes) -> str:
        for encoding in ("utf-8-sig", "utf-8", "latin-1"):
            try:
                return raw_bytes.decode(encoding)
            except UnicodeDecodeError:
                continue
        return ""

    def _normalize_text_for_profile(self, value: str) -> str:
        upper = unicodedata.normalize("NFKD", value.upper())
        without_accents = "".join(ch for ch in upper if not unicodedata.combining(ch))
        return re.sub(r"\s+", " ", without_accents).strip()
