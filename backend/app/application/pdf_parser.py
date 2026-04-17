import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO

from pypdf import PdfReader

from app.application.csv_parser import _parse_amount
from app.application.errors import InvalidFileContentError
from app.application.models import NormalizedTransaction
from app.application.pdf_layout_inference import PdfLayoutInference, infer_pdf_layout

MONTH_TO_NUMBER = {
    "JAN": 1,
    "FEV": 2,
    "MAR": 3,
    "ABR": 4,
    "MAI": 5,
    "JUN": 6,
    "JUL": 7,
    "AGO": 8,
    "SET": 9,
    "OUT": 10,
    "NOV": 11,
    "DEZ": 12,
}
MONTH_PATTERN = "|".join(MONTH_TO_NUMBER)
DATE_HEADER_PATTERN = re.compile(rf"^(?P<day>\d{{2}})\s+(?P<month>{MONTH_PATTERN})\s+(?P<year>\d{{4}})(?P<rest>.*)$")
AMOUNT_PATTERN = re.compile(r"^-?\d+(?:\.\d{3})*,\d{2}$")
INLINE_ROW_PATTERN = re.compile(
    r"^(?P<date>\d{2}/\d{2}/\d{4})\s+(?P<description>.+?)\s+(?P<amount>-?\d+(?:\.\d{3})*,\d{2})$"
)

INFLOW_HINTS = (
    "TRANSFERENCIA RECEBIDA",
    "RECEBIMENTO",
    "ESTORNO",
    "CREDITO",
    "SALARIO",
)
OUTFLOW_HINTS = (
    "TRANSFERENCIA ENVIADA",
    "PAGAMENTO",
    "COMPRA",
    "DEBITO",
    "SAIDA",
    "TARIFA",
    "SAQUE",
)
IGNORED_LINE_PREFIXES = (
    "SALDO INICIAL",
    "SALDO FINAL",
    "MOVIMENTACOES",
    "EXTRATO GERADO DIA",
    "OUVIDORIA:",
)
IGNORED_LINE_TOKENS = (
    "VALORES EM R",
    "CNPJ AGENCIA CONTA",
)


@dataclass(frozen=True)
class PdfParseResult:
    transactions: list[NormalizedTransaction]
    layout: PdfLayoutInference


def parse_pdf_transactions(raw_bytes: bytes) -> PdfParseResult:
    page_texts = _extract_pdf_page_texts(raw_bytes)
    joined_text = "\n".join(page_texts)
    layout = infer_pdf_layout(joined_text)
    lines = _flatten_statement_lines(page_texts)
    transactions = _parse_grouped_statement_lines(lines)
    if not transactions:
        transactions, inline_candidates = _parse_inline_statement_rows(lines)
        if not transactions:
            if inline_candidates > 0:
                raise InvalidFileContentError(
                    "PDF text was extracted, but transactions are in an unsupported table layout."
                )
            raise InvalidFileContentError(
                "PDF text was extracted, but no recognizable transaction row pattern was found."
            )

    return PdfParseResult(transactions=transactions, layout=layout)


def _extract_pdf_page_texts(raw_bytes: bytes) -> list[str]:
    try:
        reader = PdfReader(BytesIO(raw_bytes))
    except Exception as exc:  # pragma: no cover - defensive guard for parser internals
        raise InvalidFileContentError("Unable to read PDF bytes.") from exc

    pages = [(page.extract_text() or "").strip() for page in reader.pages]
    pages = [item for item in pages if item]
    if not pages:
        raise InvalidFileContentError("PDF does not contain extractable text.")
    return pages


def _flatten_statement_lines(page_texts: list[str]) -> list[str]:
    lines: list[str] = []
    for page_text in page_texts:
        for line in page_text.splitlines():
            cleaned = " ".join(line.split())
            if cleaned:
                lines.append(cleaned)
    return lines


def _parse_grouped_statement_lines(lines: list[str]) -> list[NormalizedTransaction]:
    transactions: list[NormalizedTransaction] = []
    current_date: str | None = None
    current_section_hint: str | None = None
    description_parts: list[str] = []

    for line in lines:
        normalized_line = _normalize_text(line)
        date_match = DATE_HEADER_PATTERN.match(normalized_line)
        if date_match:
            current_date = _build_iso_date(
                year=date_match.group("year"),
                month_abbrev=date_match.group("month"),
                day=date_match.group("day"),
            )
            current_section_hint = None
            description_parts = []
            maybe_hint = _section_hint(date_match.group("rest"))
            if maybe_hint:
                current_section_hint = maybe_hint
            continue

        if current_date is None:
            continue

        maybe_hint = _section_hint(normalized_line)
        if maybe_hint:
            current_section_hint = maybe_hint
            description_parts = []
            continue

        if _should_ignore_line(normalized_line):
            description_parts = []
            continue

        if AMOUNT_PATTERN.fullmatch(line):
            if not description_parts:
                continue
            amount = _parse_amount(line)
            description = " ".join(description_parts).strip()
            signed_amount = _apply_sign_hints(amount=amount, description=description, section_hint=current_section_hint)
            transactions.append(
                NormalizedTransaction(
                    date=current_date,
                    description=description,
                    amount=signed_amount,
                    type="inflow" if signed_amount >= 0 else "outflow",
                )
            )
            description_parts = []
            continue

        description_parts.append(line.strip())

    return transactions


def _parse_inline_statement_rows(lines: list[str]) -> tuple[list[NormalizedTransaction], int]:
    transactions: list[NormalizedTransaction] = []
    candidates = 0

    for line in lines:
        match = INLINE_ROW_PATTERN.match(line)
        if not match:
            continue
        candidates += 1
        raw_description = match.group("description").strip()
        if not raw_description:
            continue

        amount = _parse_amount(match.group("amount"))
        signed_amount = _apply_sign_hints(
            amount=amount,
            description=raw_description,
            section_hint=None,
        )
        transactions.append(
            NormalizedTransaction(
                date=_parse_slash_date(match.group("date")),
                description=raw_description,
                amount=signed_amount,
                type="inflow" if signed_amount >= 0 else "outflow",
            )
        )

    return transactions, candidates


def _section_hint(text: str) -> str | None:
    normalized = _normalize_text(text)
    if "TOTAL DE ENTRADAS" in normalized:
        return "inflow"
    if "TOTAL DE SAIDAS" in normalized:
        return "outflow"
    return None


def _should_ignore_line(normalized_line: str) -> bool:
    if not normalized_line:
        return True
    if normalized_line in {"-", "--"}:
        return True
    if re.fullmatch(r"\d+\s+DE\s+\d+", normalized_line):
        return True
    if any(normalized_line.startswith(prefix) for prefix in IGNORED_LINE_PREFIXES):
        return True
    if any(token in normalized_line for token in IGNORED_LINE_TOKENS):
        return True
    return False


def _apply_sign_hints(amount: float, description: str, section_hint: str | None) -> float:
    normalized_description = _normalize_text(description)
    if any(token in normalized_description for token in INFLOW_HINTS):
        return abs(amount)
    if any(token in normalized_description for token in OUTFLOW_HINTS):
        return -abs(amount)
    if section_hint == "inflow":
        return abs(amount)
    if section_hint == "outflow":
        return -abs(amount)
    return amount


def _parse_slash_date(raw: str) -> str:
    try:
        return datetime.strptime(raw, "%d/%m/%Y").strftime("%Y-%m-%d")
    except ValueError as exc:
        raise InvalidFileContentError(f"Invalid date value in PDF statement: {raw!r}.") from exc


def _build_iso_date(year: str, month_abbrev: str, day: str) -> str:
    month_value = MONTH_TO_NUMBER.get(month_abbrev)
    if month_value is None:
        raise InvalidFileContentError(f"Invalid month value in PDF statement: {month_abbrev!r}.")
    try:
        return datetime(int(year), month_value, int(day)).strftime("%Y-%m-%d")
    except ValueError as exc:
        raise InvalidFileContentError(f"Invalid date value in PDF statement: {day}/{month_abbrev}/{year}.") from exc


def _normalize_text(value: str) -> str:
    upper = unicodedata.normalize("NFKD", value.upper())
    without_accents = "".join(ch for ch in upper if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", without_accents).strip()
