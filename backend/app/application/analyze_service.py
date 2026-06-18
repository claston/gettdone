import logging
import re
import unicodedata
from typing import Callable
from uuid import uuid4

from app.application.bank_catalog import resolve_bank_code_from_name
from app.application.bank_identity import resolve_bank_name
from app.application.bank_resolver import DEFAULT_BANK_CODE, resolve_bank_code
from app.application.conversion_pipeline import ConversionPipeline
from app.application.ingestion import SUPPORTED_DOCUMENT_EXTENSIONS, ingest_uploaded_document
from app.application.models import TransactionRow
from app.application.parsers.service import ParsingService
from app.application.pdf_parser import parse_pdf_transactions
from app.application.repositories import AnalysisRepository
from app.application.structured_conversion import build_structured_conversion_result_from_analysis_data
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

SUPPORTED_EXTENSIONS = SUPPORTED_DOCUMENT_EXTENSIONS
logger = logging.getLogger(__name__)


class AnalyzeService:
    def __init__(
        self,
        storage: AnalysisRepository,
        parser: ParsingService | None = None,
        pipeline: ConversionPipeline | None = None,
    ) -> None:
        self.storage = storage
        self.pipeline = pipeline or ConversionPipeline(
            parser=parser or ParsingService(),
            resolve_opening_balance=lambda rows, extracted_text: self._resolve_opening_balance(
                rows,
                extracted_text=extracted_text,
            ),
            is_balance_metadata_row=self._is_balance_metadata_row,
            resolve_closing_balance=lambda rows, opening_balance: self._resolve_closing_balance(
                rows,
                opening_balance=opening_balance,
            ),
            build_pdf_processing_metrics=self._build_pdf_processing_metrics,
            resolve_bank_name=lambda extension, layout_inference_name, extracted_text: self._resolve_bank_name(
                extension=extension,
                layout_inference_name=layout_inference_name,
                extracted_text=extracted_text,
            ),
            extract_bank_account_metadata=self._extract_bank_account_metadata,
            resolve_inferred_bank_code=lambda extension, layout_inference_name, bank_name: (
                self._resolve_inferred_bank_code(
                    extension=extension,
                    layout_inference_name=layout_inference_name,
                    bank_name=bank_name,
                )
            ),
            resolve_ofx_account_type=lambda extension, filename, raw_bytes, extracted_text, layout_inference_name: (
                self._resolve_ofx_account_type(
                    extension=extension,
                    filename=filename,
                    raw_bytes=raw_bytes,
                    extracted_text=extracted_text,
                    layout_inference_name=layout_inference_name,
                )
            ),
        )

    def analyze(
        self,
        filename: str,
        raw_bytes: bytes,
        on_ocr_progress: Callable[[int, int], None] | None = None,
        max_ocr_pages: int | None = None,
        analysis_id: str | None = None,
    ) -> AnalyzeResponse:
        document = ingest_uploaded_document(filename=filename, raw_bytes=raw_bytes)
        extension = document.file_type
        logger.info(
            "analyze_start extension=%s size_bytes=%d filename=%s",
            extension,
            document.size_bytes,
            (filename or "")[:120],
        )

        analysis_id = (analysis_id or "").strip() or f"an_{uuid4().hex[:12]}"
        pipeline_result = self.pipeline.run_document(
            document=document,
            analysis_id=analysis_id,
            on_ocr_progress=on_ocr_progress,
            max_ocr_pages=max_ocr_pages,
            pdf_parser=parse_pdf_transactions,
        )
        analysis_data = pipeline_result.analysis_data
        analysis_data.structured_result = build_structured_conversion_result_from_analysis_data(analysis_data)
        expires_at = self.storage.save_analysis(analysis_data)
        logger.info(
            "analyze_done analysis_id=%s extension=%s total_ms=%.3f parse_ms=%.3f tx_count=%d layout=%s parser=%s",
            analysis_id,
            extension,
            (analysis_data.pdf_processing_metrics or {}).get("total_ms", 0.0),
            pipeline_result.parse_ms,
            analysis_data.transactions_total,
            analysis_data.layout_inference_name or "",
            (analysis_data.pdf_processing_metrics or {}).get("selected_parser", ""),
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
            pdf_processing_metrics=analysis_data.pdf_processing_metrics,
        )
        if review_insight is not None:
            insights.append(review_insight)

        return AnalyzeResponse(
            analysis_id=analysis_id,
            file_type=extension,
            semantic_type=analysis_data.semantic_type,
            semantic_confidence=analysis_data.semantic_confidence,
            semantic_evidence=analysis_data.semantic_evidence or [],
            transactions_total=analysis_data.transactions_total,
            total_inflows=analysis_data.total_inflows,
            total_outflows=analysis_data.total_outflows,
            net_total=analysis_data.net_total,
            operational_summary=OperationalSummary(
                total_volume=pipeline_result.operational_summary.total_volume,
                inflow_count=pipeline_result.operational_summary.inflow_count,
                outflow_count=pipeline_result.operational_summary.outflow_count,
                reconciled_entries=pipeline_result.operational_summary.reconciled_entries,
                unmatched_entries=pipeline_result.operational_summary.unmatched_entries,
            ),
            reconciliation=ReconciliationSummary(
                matched_groups=analysis_data.matched_groups,
                reversed_entries=analysis_data.reversed_entries,
                potential_duplicates=analysis_data.potential_duplicates,
            ),
            categories=[
                CategorySummary(
                    category="Outros",
                    total=analysis_data.net_total,
                    count=analysis_data.transactions_total,
                )
            ],
            top_expenses=[
                TopExpense(
                    description=row.description,
                    amount=row.amount,
                    date=row.date,
                    category="Outros",
                )
                for row in pipeline_result.top_expenses_rows
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
                for row in analysis_data.preview_transactions
            ],
            preview_before_after=[
                BeforeAfterPreview(
                    date=row.date,
                    description_before=row.description_before,
                    description_after=row.description_after,
                    amount_before=row.amount_before,
                    amount_after=row.amount_after,
                )
                for row in analysis_data.preview_before_after
            ],
            expires_at=expires_at,
            updated_at=analysis_data.updated_at,
            layout_inference_name=analysis_data.layout_inference_name,
            layout_inference_confidence=analysis_data.layout_inference_confidence,
            pdf_processing_metrics=analysis_data.pdf_processing_metrics,
            opening_balance=analysis_data.opening_balance,
            closing_balance=analysis_data.closing_balance,
            bank_name=analysis_data.bank_name,
            bank_branch=analysis_data.bank_branch,
            account_number=analysis_data.account_number,
            bank_code=analysis_data.bank_code,
        )

    def _resolve_opening_balance(self, rows: list[TransactionRow], *, extracted_text: str | None = None) -> float | None:
        for row in rows:
            normalized_description = self._normalize_text_for_profile(row.description)
            if self._is_opening_balance_description(normalized_description):
                return round(float(row.amount), 2)

        amount_pattern = r"([\-+]?\d{1,3}(?:[.\s]\d{3})*(?:,\d{2})|[\-+]?\d+(?:,\d{2})?)"
        opening_label_pattern = re.compile(
            r"S\s*A\s*L\s*D\s*O\s+(?:A\s*N\s*T\s*E\s*R\s*I\s*O\s*R|I\s*N\s*I\s*C\s*I\s*A\s*L)",
            flags=re.IGNORECASE,
        )
        for raw_line in (extracted_text or "").splitlines():
            normalized_line = self._normalize_text_for_profile(raw_line)
            if (
                "SALDO ANTERIOR" not in normalized_line
                and "SALDO INICIAL" not in normalized_line
                and not opening_label_pattern.search(raw_line)
            ):
                continue
            match = re.search(amount_pattern, raw_line)
            if not match:
                continue
            try:
                raw_amount = match.group(1).replace(" ", "").replace(".", "").replace(",", ".")
                return round(float(raw_amount), 2)
            except ValueError:
                continue
        for row in rows:
            if row.running_balance is None:
                continue
            return round(float(row.running_balance) - float(row.amount), 2)
        return None

    def _resolve_closing_balance(self, rows: list[TransactionRow], *, opening_balance: float | None = None) -> float | None:
        last_balance_index: int | None = None
        last_running_balance: float | None = None
        for index, row in enumerate(rows):
            if row.running_balance is None:
                continue
            last_balance_index = index
            last_running_balance = round(float(row.running_balance), 2)
        if last_running_balance is not None and last_balance_index is not None:
            trailing_amount = sum(float(row.amount) for row in rows[last_balance_index + 1 :])
            return round(last_running_balance + trailing_amount, 2)

        if opening_balance is not None:
            transaction_total = sum(
                float(row.amount)
                for row in rows
                if not self._is_opening_balance_description(row.description)
            )
            return round(float(opening_balance) + transaction_total, 2)
        return None

    def _is_opening_balance_description(self, description: str) -> bool:
        normalized = self._normalize_text_for_profile(description)
        return "SALDO ANTERIOR" in normalized or "SALDO INICIAL" in normalized

    def _is_balance_metadata_row(self, rows: list[TransactionRow], index: int) -> bool:
        row = rows[index]
        if self._is_opening_balance_description(row.description):
            return True

        normalized = self._normalize_text_for_profile(row.description)
        if "SALDO" not in normalized:
            return False
        if row.running_balance is not None:
            return False
        if index == 0:
            return False

        previous_balance = rows[index - 1].running_balance
        if previous_balance is None:
            return False
        if abs(float(row.amount) - float(previous_balance)) > 0.05:
            return False

        return True

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
            "extraction_provider": str(parse_metrics.get("extraction_provider", "")),
            "textract_used": int(parse_metrics.get("textract_used", 0)),
            "textract_enabled": int(parse_metrics.get("textract_enabled", 0)),
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

    def _resolve_bank_name(
        self,
        *,
        extension: str,
        layout_inference_name: str | None,
        extracted_text: str | None,
    ) -> str | None:
        if extension != "pdf":
            return None
        return resolve_bank_name(
            layout_inference_name=layout_inference_name,
            extracted_text=extracted_text,
        )

    def _resolve_inferred_bank_code(
        self,
        *,
        extension: str,
        layout_inference_name: str | None,
        bank_name: str | None,
    ) -> str | None:
        if extension != "pdf":
            return None
        code = resolve_bank_code(layout_inference_name=layout_inference_name)
        if code == DEFAULT_BANK_CODE and bank_name:
            code = resolve_bank_code_from_name(bank_name) or DEFAULT_BANK_CODE
        return None if code == DEFAULT_BANK_CODE else code

    def _extract_bank_account_metadata(self, extracted_text: str | None) -> tuple[str | None, str | None]:
        raw_lines = [line.strip() for line in str(extracted_text or "").splitlines() if line.strip()]
        if not raw_lines:
            return None, None
        return self._extract_bank_account_metadata_from_header(raw_lines)

        branch_patterns = (
            r"\bAG(?:E|Ê)NCIA\s*[:\-]?\s*(\d{3,6}(?:[-.]\d)?)\b",
            r"\bAG\s*[:\-]?\s*(\d{3,6}(?:[-.]\d)?)\b",
        )
        account_patterns = (
            r"\bCONTA(?:\s+CORRENTE)?\s*[:\-]\s*(\d{4,14}(?:[-.]\d)?)\b",
            r"\bC\/C\s*[:\-]\s*(\d{4,14}(?:[-.]\d)?)\b",
            r"\bCC\s*[:\-]\s*(\d{4,14}(?:[-.]\d)?)\b",
        )

        # Prefer account metadata from header-like lines (before transaction body),
        # and support both "CONTA: 12345-6" and "CONTA 12345-6".
        raw_lines = [line.strip() for line in str(extracted_text or "").splitlines() if line.strip()]
        header_lines: list[str] = []
        for line in raw_lines[:80]:
            normalized_line = self._normalize_text_for_profile(line)
            if "LANCAMENTOS" in normalized_line or "MOVIMENTACOES" in normalized_line:
                break
            if re.match(r"^\d{1,2}[/-]\d{1,2}\b", line):
                continue
            header_lines.append(line)
        if header_lines:
            search_scope = self._normalize_text_for_profile("\n".join(header_lines))
        else:
            search_scope = ""

        branch_patterns = (
            r"\bAG(?:E|Ê)NCIA\s*(?:[:\-]\s*|\s+)(\d{3,6}(?:[-.]\d)?)\b",
            r"\bAG\s*(?:[:\-]\s*|\s+)(\d{3,6}(?:[-.]\d)?)\b",
        )
        account_patterns = (
            r"\bCONTA(?:\s+CORRENTE)?\s*(?:[:\-]\s*|\s+)(\d{4,14}(?:[-.]\d)?)\b",
            r"\bC\/C\s*(?:[:\-]\s*|\s+)(\d{4,14}(?:[-.]\d)?)\b",
            r"\bCC\s*(?:[:\-]\s*|\s+)(\d{4,14}(?:[-.]\d)?)\b",
        )

        for pattern in branch_patterns:
            match = re.search(pattern, search_scope)
            if match:
                branch = self._normalize_account_identifier(match.group(1))
                if branch:
                    break

        for pattern in account_patterns:
            match = re.search(pattern, search_scope)
            if match:
                account = self._normalize_account_identifier(match.group(1))
                if account:
                    break

        return branch, account

    def _extract_bank_account_metadata_from_header(self, raw_lines: list[str]) -> tuple[str | None, str | None]:
        header_lines: list[str] = []
        for line in raw_lines[:80]:
            normalized_line = self._normalize_text_for_profile(line)
            if (
                "LANCAMENTOS" in normalized_line
                or "MOVIMENTACOES" in normalized_line
                or ("DATA" in normalized_line and "VALOR" in normalized_line)
            ):
                break
            if re.match(r"^\d{1,2}[/-]\d{1,2}\b", line):
                break
            header_lines.append(line)
        if not header_lines:
            return None, None

        normalized_lines = [self._normalize_text_for_profile(line) for line in header_lines]
        branch: str | None = None
        account: str | None = None

        def _extract_value_from_line(
            line: str,
            *,
            min_len: int,
            max_len: int,
            label_pattern: str | None = None,
        ) -> str | None:
            if label_pattern:
                match = re.search(
                    rf"{label_pattern}\s*[:\-]?\s*(\d{{{min_len},{max_len}}}(?:[-.]\d)?)\b",
                    line,
                )
            else:
                match = re.search(rf"\b(\d{{{min_len},{max_len}}}(?:[-.]\d)?)\b", line)
            if not match:
                return None
            return self._normalize_account_identifier(match.group(1))

        for idx, line in enumerate(normalized_lines):
            if branch is None and re.search(r"\bAGENCIA\b", line):
                candidate = _extract_value_from_line(
                    line,
                    min_len=3,
                    max_len=8,
                    label_pattern=r"\bAGENCIA\b",
                )
                if candidate is None and idx + 1 < len(normalized_lines):
                    candidate = _extract_value_from_line(normalized_lines[idx + 1], min_len=3, max_len=8)
                if candidate:
                    branch = candidate

            if account is None and re.search(r"\b(CONTA(?:\s+CORRENTE)?|C/C|CC)\b", line):
                candidate = _extract_value_from_line(
                    line,
                    min_len=4,
                    max_len=14,
                    label_pattern=r"\b(?:CONTA(?:\s+CORRENTE)?|C/C|CC)\b",
                )
                allow_next_line = bool(
                    re.search(r"^\s*(?:CONTA(?:\s+CORRENTE)?|C/C|CC)\s*:?\s*$", line)
                )
                if candidate is None and allow_next_line and idx + 1 < len(normalized_lines):
                    candidate = _extract_value_from_line(normalized_lines[idx + 1], min_len=4, max_len=14)
                if candidate:
                    account = candidate

            if branch and account:
                break

        return branch, account

    def _extract_account_metadata_scope(self, normalized_text: str) -> str:
        if not normalized_text:
            return ""
        limit_markers = (" LANCAMENTOS ", " MOVIMENTACOES ", " EXTRATO ")
        cut_index = len(normalized_text)
        for marker in limit_markers:
            idx = normalized_text.find(marker)
            if idx > 0:
                cut_index = min(cut_index, idx)
        return normalized_text[: min(cut_index, 1400)]

    def _normalize_account_identifier(self, value: str | None) -> str | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        digits = re.sub(r"\D", "", raw)
        if len(digits) < 3:
            return None
        if "-" in raw or "." in raw:
            cleaned = raw.replace(".", "-")
            cleaned = re.sub(r"[^0-9-]", "", cleaned)
            cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-")
            return cleaned or digits
        return digits

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
