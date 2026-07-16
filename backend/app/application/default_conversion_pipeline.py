import logging
import re
import unicodedata

from app.application.bank_catalog import resolve_bank_code_from_name
from app.application.bank_identity import resolve_bank_name
from app.application.bank_resolver import DEFAULT_BANK_CODE, resolve_bank_code
from app.application.conversion_pipeline import ConversionPipeline
from app.application.models import TransactionRow
from app.application.normalization.balance import uses_descending_running_balance
from app.application.parsers.service import ParsingService
from app.application.pdf_parser import parse_pdf_transactions

logger = logging.getLogger(__name__)

__all__ = [
    "build_default_conversion_pipeline",
    "parse_pdf_transactions",
]


def build_default_conversion_pipeline(
    *,
    parser: ParsingService | None = None,
) -> ConversionPipeline:
    return ConversionPipeline(
        parser=parser or ParsingService(),
        resolve_opening_balance=lambda rows, extracted_text, layout_inference_name: _resolve_opening_balance(
            rows,
            extracted_text=extracted_text,
            layout_name=layout_inference_name,
        ),
        is_balance_metadata_row=_is_balance_metadata_row,
        resolve_closing_balance=lambda rows, opening_balance, layout_inference_name: _resolve_closing_balance(
            rows,
            opening_balance=opening_balance,
            layout_name=layout_inference_name,
        ),
        build_pdf_processing_metrics=_build_pdf_processing_metrics,
        resolve_bank_name=lambda extension, layout_inference_name, extracted_text: _resolve_bank_name(
            extension=extension,
            layout_inference_name=layout_inference_name,
            extracted_text=extracted_text,
        ),
        extract_bank_account_metadata=_extract_bank_account_metadata,
        resolve_inferred_bank_code=lambda extension, layout_inference_name, bank_name: _resolve_inferred_bank_code(
            extension=extension,
            layout_inference_name=layout_inference_name,
            bank_name=bank_name,
        ),
        resolve_ofx_account_type=lambda extension, filename, raw_bytes, extracted_text, layout_inference_name: (
            _resolve_ofx_account_type(
                extension=extension,
                filename=filename,
                raw_bytes=raw_bytes,
                extracted_text=extracted_text,
                layout_inference_name=layout_inference_name,
            )
        ),
    )


def _resolve_opening_balance(
    rows: list[TransactionRow],
    *,
    extracted_text: str | None = None,
    layout_name: str | None = None,
) -> float | None:
    for row in rows:
        normalized_description = _normalize_text_for_profile(row.description)
        if _is_opening_balance_description(normalized_description):
            return round(float(row.amount), 2)

    amount_pattern = r"([\-+]?\d{1,3}(?:[.\s]\d{3})*(?:,\d{2})|[\-+]?\d+(?:,\d{2})?)"
    opening_label_pattern = re.compile(
        r"S\s*A\s*L\s*D\s*O\s+(?:A\s*N\s*T\s*E\s*R\s*I\s*O\s*R|I\s*N\s*I\s*C\s*I\s*A\s*L)",
        flags=re.IGNORECASE,
    )
    for raw_line in (extracted_text or "").splitlines():
        normalized_line = _normalize_text_for_profile(raw_line)
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
    if uses_descending_running_balance(layout_name):
        return None
    balance_rows = reversed(rows) if uses_descending_running_balance(layout_name) else rows
    for row in balance_rows:
        if row.running_balance is None:
            continue
        return round(float(row.running_balance) - float(row.amount), 2)
    return None


def _resolve_closing_balance(
    rows: list[TransactionRow],
    *,
    opening_balance: float | None = None,
    layout_name: str | None = None,
) -> float | None:
    if uses_descending_running_balance(layout_name):
        for row in rows:
            if row.running_balance is None:
                continue
            return round(float(row.running_balance), 2)

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
            if not _is_opening_balance_description(row.description)
        )
        return round(float(opening_balance) + transaction_total, 2)
    return None


def _is_opening_balance_description(description: str) -> bool:
    normalized = _normalize_text_for_profile(description)
    return "SALDO ANTERIOR" in normalized or "SALDO INICIAL" in normalized


def _is_balance_metadata_row(rows: list[TransactionRow], index: int) -> bool:
    row = rows[index]
    if _is_opening_balance_description(row.description):
        return True

    normalized = _normalize_text_for_profile(row.description)
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
        "multiline_candidates_count": int(parse_metrics.get("multiline_candidates_count", 0)),
        "multiline_transactions_count": int(parse_metrics.get("multiline_transactions_count", 0)),
        "selected_parser": str(parse_metrics.get("selected_parser", "unknown")),
        "parser_selection_reason": str(parse_metrics.get("parser_selection_reason", "")),
        "extraction_provider": str(parse_metrics.get("extraction_provider", "")),
        "textract_used": int(parse_metrics.get("textract_used", 0)),
        "textract_enabled": int(parse_metrics.get("textract_enabled", 0)),
        "textract_attempted": int(parse_metrics.get("textract_attempted", 0)),
        "textract_error_type": str(parse_metrics.get("textract_error_type", "")),
        "native_text_detected": int(parse_metrics.get("native_text_detected", 0)),
        "inline_decision": str(parse_metrics.get("inline_decision", "")),
        "tabular_decision": str(parse_metrics.get("tabular_decision", "")),
        "columnar_decision": str(parse_metrics.get("columnar_decision", "")),
        "multiline_decision": str(parse_metrics.get("multiline_decision", "")),
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
        "canonical_source_parser_multiline_count": int(
            parse_metrics.get("canonical_source_parser_multiline_count", 0)
        ),
        "canonical_source_parser_types_count": int(parse_metrics.get("canonical_source_parser_types_count", 0)),
        "canonical_source_parser_types": str(parse_metrics.get("canonical_source_parser_types", "")),
        "canonical_source_parser_types_list": [
            item
            for item in str(parse_metrics.get("canonical_source_parser_types_list", "")).split("|")
            if item.strip()
        ],
    }


def _resolve_bank_name(*, extension: str, layout_inference_name: str | None, extracted_text: str | None) -> str | None:
    if extension != "pdf":
        return None
    return resolve_bank_name(
        layout_inference_name=layout_inference_name,
        extracted_text=extracted_text,
    )


def _resolve_inferred_bank_code(
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


def _extract_bank_account_metadata(extracted_text: str | None) -> tuple[str | None, str | None]:
    raw_lines = [line.strip() for line in str(extracted_text or "").splitlines() if line.strip()]
    if not raw_lines:
        return None, None
    return _extract_bank_account_metadata_from_header(raw_lines)


def _extract_bank_account_metadata_from_header(raw_lines: list[str]) -> tuple[str | None, str | None]:
    header_lines: list[str] = []
    for line in raw_lines[:80]:
        normalized_line = _normalize_text_for_profile(line)
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

    normalized_lines = [_normalize_text_for_profile(line) for line in header_lines]
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
        return _normalize_account_identifier(match.group(1))

    for idx, line in enumerate(normalized_lines):
        if branch is None and re.search(r"\bAGENCIA\b", line):
            candidate = _extract_value_from_line(
                line,
                min_len=3,
                max_len=8,
                label_pattern=r"\bAGENCIA\b",
            )
            if candidate is None and idx + 1 < len(normalized_lines):
                candidate = _extract_header_identifier_from_next_line(
                    normalized_lines[idx + 1],
                    min_len=3,
                    max_len=8,
                )
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
                candidate = _extract_header_identifier_from_next_line(
                    normalized_lines[idx + 1],
                    min_len=4,
                    max_len=14,
                )
            if candidate:
                account = candidate

        if branch and account:
            break

    return branch, account


def _extract_header_identifier_from_next_line(line: str, *, min_len: int, max_len: int) -> str | None:
    if not _looks_like_standalone_account_identifier(line, min_len=min_len, max_len=max_len):
        return None
    return _normalize_account_identifier(line)


def _looks_like_standalone_account_identifier(line: str, *, min_len: int, max_len: int) -> bool:
    normalized_line = str(line or "").strip()
    if not normalized_line:
        return False
    if ":" in normalized_line:
        return False
    if re.search(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b", normalized_line):
        return False
    if re.search(r"\b(?:DATA|CLIENTE|CONTA|AGENCIA|OPERACAO|DOCUMENTO|PRODUTO|CPF|CNPJ)\b", normalized_line):
        return False
    return bool(re.fullmatch(rf"\d{{{min_len},{max_len}}}(?:[-.]\d)?", normalized_line))


def _normalize_account_identifier(value: str | None) -> str | None:
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


def _resolve_ofx_account_type(
    *,
    extension: str,
    filename: str,
    raw_bytes: bytes,
    extracted_text: str | None,
    layout_inference_name: str | None,
) -> str | None:
    if extension == "ofx":
        decoded = _decode_optional_text(raw_bytes)
        normalized = decoded.upper()
        if "CREDITCARDMSGSRSV1" in normalized or "<CCSTMTRS>" in normalized:
            return "credit_card"
        if "BANKMSGSRSV1" in normalized or "<STMTRS>" in normalized:
            return "bank"
        return None

    if extension == "pdf":
        normalized = _normalize_text_for_profile((extracted_text or "") + " " + filename)
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
            "DETALHAMENTO DA FATURA",
            "PAGAMENTO E DEMAIS CREDITOS",
            "PARCELAMENTOS",
        )
        card_matches = sum(1 for token in card_indicators if token in normalized)
        has_card_window = "TRANSACOES DE" in normalized and " A " in normalized
        has_invoice_sections = (
            "DETALHAMENTO DA FATURA" in normalized
            and "PAGAMENTO E DEMAIS CREDITOS" in normalized
            and "DESPESAS" in normalized
        )

        if (
            card_matches >= 2
            and (has_card_window or "TOTAL A PAGAR" in normalized or has_invoice_sections)
            and not has_bank_indicators
        ):
            return "credit_card"
        return None

    return None


def _decode_optional_text(raw_bytes: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    return ""


def _normalize_text_for_profile(value: str) -> str:
    upper = unicodedata.normalize("NFKD", value.upper())
    without_accents = "".join(ch for ch in upper if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", without_accents).strip()
