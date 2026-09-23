from __future__ import annotations

import re
import unicodedata
from collections import Counter
from functools import lru_cache

from app.application.bank_catalog import load_bank_catalog
from app.application.conversion.canonical_layout_v2 import (
    public_layout_fingerprint_labels,
    sanitize_layout_line,
)
from app.application.document_extraction_models import ExtractedLine

_TABLE_HEADER_TERMS = ("DATA", "HISTORICO", "VALOR", "SALDO")
_HEADER_TERMS = ("DATA", "EFETIVA", "DOCUMENTO", "HISTORICO", "VALOR", "SALDO")
_HEADER_FRAGMENT_TERMS = frozenset(
    {"D", "DATA", "EFETIVA", "DOCUMENTO", "OCUMENTO", "HISTORICO", "MOV", "NR", "SALDO", "VALOR"}
)
_SAFE_PRETABLE_PHRASES = (
    "EXTRATO",
    "GERENCIADOR CAIXA",
    "PERIODO DO EXTRATO",
    "SALDO ANTERIOR",
    "SALDO INICIAL",
)
_OPERATION_TERMS = (
    "PIX",
    "TED",
    "DOC",
    "CREDITO",
    "CRED",
    "DEBITO",
    "DEB",
    "TARIFA",
    "TAR",
    "TRANSFERENCIA",
    "TRANSF",
    "PAGAMENTO",
    "PAG",
    "RECEBIMENTO",
    "RECEB",
    "DEPOSITO",
    "DEP",
    "SAQUE",
    "COMPRA",
    "BOLETO",
    "APLICACAO",
    "APLIC",
    "BLOQ",
    "RESGATE",
    "ESTORNO",
    "COB",
    "IOF",
    "JUROS",
    "SALDO DIA",
)
_OPERATION_PHRASES = tuple(
    sorted(
        {
            "CRED PIX QR",
            "CREDITO TRANSF INTERNET",
            "DEB PIX CHAVE",
            "DEBITO AUTORIZADO",
            "ENVIO DE TED",
            "PAG BOLETO",
            "PIX ENVIADO",
            "PIX RECEBIDO",
            "RECEBIMENTO TED",
            "TRANSFERENCIA RECEBIDA",
            *_OPERATION_TERMS,
        },
        key=len,
        reverse=True,
    )
)
_PRIVATE_NAME_LABEL_PATTERN = re.compile(
    r"\b(CLIENTE|TITULAR|NOME|RAZAO SOCIAL|BENEFICIARIO|PAGADOR|RECEBEDOR|FAVORECIDO)\s*:?\s*"
    r"(.*?)(?=\s+\b(?:CPF|CNPJ|AGENCIA|CONTA|ENDERECO)\b|$)"
)
_PRIVATE_NAME_LABEL_PREFIX_PATTERN = re.compile(
    r"^\s*(?:CLIENTE|TITULAR|NOME|RAZAO SOCIAL|BENEFICIARIO|PAGADOR|RECEBEDOR|FAVORECIDO)\b"
)
_CPF_PATTERN = re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")
_CNPJ_PATTERN = re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b")
_MASKED_CPF_PATTERN = re.compile(r"(?<!\d)(?:\*{3}|\d{3})\.\d{3}\.\d{3}-(?:\*{2}|\d{2})(?!\d)")
_EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_PHONE_PATTERN = re.compile(r"(?<!\d)(?:\+?55\s*)?\(?\d{2}\)?\s*9?\d{4}[-\s]?\d{4}(?!\d)")
_ACCOUNT_PATTERN = re.compile(r"\b(CONTA(?:\s+CORRENTE)?)\s*:?\s*[A-Z0-9./-]+")
_AGENCY_PATTERN = re.compile(r"\b(AGENCIA)\s*:?\s*[A-Z0-9./-]+")
_ADDRESS_PATTERN = re.compile(r"^\s*(ENDERECO|LOGRADOURO)\s*:?", re.IGNORECASE)
_PAGE_PATTERN = re.compile(r"^PAGINA\s+\d+\s+(?:DE|/)\s*\d+$")
_DATE_PATTERN = re.compile(r"(?<!\d)\d{1,2}[/.-]\d{1,2}(?:[/.-]\d{2,4})?(?!\d)")
_TIME_PATTERN = re.compile(r"(?<!\d)\d{1,2}:\d{2}(?::\d{2})?(?!\d)")
_AMOUNT_PATTERN = re.compile(
    r"(?<![\d,.])(?:[-+]\s*)?(?:R\$\s*)?(?:\d{1,3}(?:\.\d{3})+|\d+),\d{2}(?:\s*[CD])?(?![\d,.])"
)
_LONG_ALPHANUMERIC_PATTERN = re.compile(r"\b(?=[A-Z0-9]*[A-Z])(?=[A-Z0-9]*\d)[A-Z0-9]{8,}\b")
_LONG_NUMBER_PATTERN = re.compile(r"(?<![\d.,])\d{4,}(?:-\d+)?(?![\d.,])")
_PROTECTED_MARKER_PATTERN = re.compile("[\ue000-\uf8ff]")


def build_semantic_layout_pages(
    pages: list[dict[str, object]],
    *,
    source_layout_lines: tuple[tuple[ExtractedLine, ...], ...] | None = None,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    safe_pages: list[dict[str, object]] = []
    labels: set[str] = set()
    row_shapes: Counter[str] = Counter()
    geometry_sources: set[str] = set()
    for page_index, page in enumerate(pages):
        raw_lines = str(page["source_text"]).splitlines()
        layout_lines = source_layout_lines[page_index] if source_layout_lines and page_index < len(source_layout_lines) else ()
        lines = [(line.text, line.bbox) for line in layout_lines] if layout_lines else [(line, None) for line in raw_lines]
        max_columns = max((len(line) for line, _ in lines), default=1)
        line_count = max(1, len(lines))
        elements: list[dict[str, object]] = []
        used_geometry = False
        in_transaction_table = False
        table_header_terms_seen: set[str] = set()
        for line_index, (raw_text, bbox) in enumerate(lines):
            normalized = _ascii_upper(raw_text)
            table_header_terms_seen.update(
                term for term in _TABLE_HEADER_TERMS if re.search(rf"\b{term}\b", normalized)
            )
            if "OCUMENTO" in normalized:
                table_header_terms_seen.add("DOCUMENTO")
            if _is_table_header(normalized) or len(table_header_terms_seen) >= 3:
                in_transaction_table = True
            safe_text = sanitize_semantic_line(
                raw_text,
                in_transaction_table=in_transaction_table,
            )
            if not safe_text:
                continue
            _, line_labels = sanitize_layout_line(raw_text)
            labels.update(line_labels)
            labels.update(public_layout_fingerprint_labels(raw_text))
            row_shapes[_line_shape(safe_text)] += 1
            geometry = _safe_bbox(bbox)
            if geometry is None:
                x = (len(raw_text) - len(raw_text.lstrip())) / max(1, max_columns)
                y = line_index / line_count
                width = min(1.0 - x, max(0.02, len(raw_text.strip()) / max(1, max_columns)))
                height = min(0.05, 1.0 / line_count)
            else:
                x, y, width, height = geometry
                used_geometry = True
            elements.append(
                {
                    "line": line_index,
                    "x": round(x, 6),
                    "y": round(y, 6),
                    "width": round(width, 6),
                    "height": round(height, 6),
                    "role": _semantic_role(safe_text),
                    "text": safe_text,
                }
            )
        position_source = "textract" if used_geometry else "estimated"
        geometry_sources.add(position_source)
        safe_pages.append(
            {
                "width": round(float(page["width"]), 3),
                "height": round(float(page["height"]), 3),
                "line_count": line_count,
                "position_source": position_source,
                "elements": elements,
            }
        )
    geometry_source = next(iter(geometry_sources)) if len(geometry_sources) == 1 else "mixed"
    signals: dict[str, object] = {
        "labels": sorted(labels),
        "geometry_source": geometry_source,
        "page_count": len(safe_pages),
        "line_shape_counts": dict(sorted(row_shapes.items())),
        "privacy_mode": "semantic_transaction_v3",
    }
    return safe_pages, signals


def sanitize_semantic_line(raw_text: str, *, in_transaction_table: bool = True) -> str:
    value = _ascii_upper(raw_text)
    if not value:
        return ""
    if _PAGE_PATTERN.fullmatch(value):
        return value
    if _ADDRESS_PATTERN.match(value):
        return f"{_ADDRESS_PATTERN.match(value).group(1)} [ENDERECO]"

    party_match = re.match(r"^(.+?)\s+(CPF|CNPJ)\s*:?\s*(.+)$", value)
    if party_match is not None and _PRIVATE_NAME_LABEL_PREFIX_PATTERN.match(party_match.group(1)) is None:
        label = party_match.group(2)
        operation_prefix = _find_operation_prefix(party_match.group(1))
        safe_prefix = f"{operation_prefix} [PARTE]" if operation_prefix else "[PARTE]"
        identifier_pattern = _CPF_PATTERN if label == "CPF" else _CNPJ_PATTERN
        identifier_match = identifier_pattern.search(party_match.group(3))
        suffix = party_match.group(3)[identifier_match.end() :].strip() if identifier_match else ""
        value = " ".join(part for part in (safe_prefix, label, f"[{label}]", suffix) if part)

    value = _mask_party_around_unlabelled_identifier(value)

    value = _PRIVATE_NAME_LABEL_PATTERN.sub(lambda match: f"{match.group(1)} [TITULAR]", value)
    value = _CPF_PATTERN.sub("[CPF]", value)
    value = _CNPJ_PATTERN.sub("[CNPJ]", value)
    value = _MASKED_CPF_PATTERN.sub("[CPF]", value)
    value = _EMAIL_PATTERN.sub("[EMAIL]", value)
    value = _PHONE_PATTERN.sub("[TELEFONE]", value)
    value = _AGENCY_PATTERN.sub(lambda match: f"{match.group(1)} [AGENCIA]", value)
    value = _ACCOUNT_PATTERN.sub(lambda match: f"{match.group(1)} [CONTA]", value)

    if not in_transaction_table and not _is_safe_pretable_line(value):
        return "[TITULAR]"
    if in_transaction_table and _looks_like_party_name(value):
        return "[PARTE]"

    protected, replacements = _protect_operational_numbers(value)
    protected = _LONG_ALPHANUMERIC_PATTERN.sub("[IDENTIFICADOR]", protected)
    protected = _LONG_NUMBER_PATTERN.sub("[IDENTIFICADOR]", protected)
    return _restore_operational_numbers(protected, replacements)


def _protect_operational_numbers(value: str) -> tuple[str, list[str]]:
    replacements: list[str] = []

    def replace(match: re.Match[str]) -> str:
        index = len(replacements)
        replacements.append(match.group(0))
        return chr(0xE000 + index)

    protected = value
    for pattern in (_AMOUNT_PATTERN, _DATE_PATTERN, _TIME_PATTERN):
        protected = pattern.sub(replace, protected)
    return protected, replacements


def _mask_party_around_unlabelled_identifier(value: str) -> str:
    matches = [
        (match, "[CNPJ]") for match in _CNPJ_PATTERN.finditer(value)
    ] + [
        (match, "[CPF]") for match in _CPF_PATTERN.finditer(value)
    ] + [
        (match, "[CPF]") for match in _MASKED_CPF_PATTERN.finditer(value)
    ]
    if not matches:
        return value
    match, replacement = min(matches, key=lambda item: item[0].start())
    prefix = value[: match.start()].strip()
    suffix = value[match.end() :].strip()
    if not prefix or re.search(r"\b(?:CPF|CNPJ)\s*:?$", prefix):
        return value
    operation_prefix = _find_operation_prefix(prefix)
    safe_prefix = f"{operation_prefix} [PARTE]" if operation_prefix else "[PARTE]"
    return " ".join(part for part in (safe_prefix, replacement, suffix) if part)


def _find_operation_prefix(value: str) -> str | None:
    matches: list[tuple[int, int, str]] = []
    for phrase in _OPERATION_PHRASES:
        match = re.search(rf"\b{re.escape(phrase)}\b", value)
        if match is not None:
            matches.append((match.start(), -len(phrase), value[: match.end()].strip()))
    if not matches:
        return None
    return min(matches)[2]


def _restore_operational_numbers(value: str, replacements: list[str]) -> str:
    return _PROTECTED_MARKER_PATTERN.sub(
        lambda match: replacements[ord(match.group(0)) - 0xE000],
        value,
    )


def _looks_like_party_name(value: str) -> bool:
    if "[" in value:
        return False
    if _contains_any(value, _HEADER_TERMS) or _contains_any(value, _OPERATION_TERMS):
        return False
    if any(character.isdigit() for character in value):
        return False
    words = re.findall(r"[A-Z]{2,}", value)
    long_words = [word for word in words if len(word) >= 3]
    return 2 <= len(words) <= 12 and len(long_words) >= 2


def _is_table_header(value: str) -> bool:
    return sum(term in value for term in _TABLE_HEADER_TERMS) >= 3


def _contains_any(value: str, terms: tuple[str, ...]) -> bool:
    return any(re.search(rf"\b{re.escape(term)}\b", value) for term in terms)


def _is_safe_pretable_line(value: str) -> bool:
    if _is_public_bank_line(value):
        return True
    if any(phrase in value for phrase in _SAFE_PRETABLE_PHRASES):
        return True
    if any(marker in value for marker in ("[CPF]", "[CNPJ]", "[AGENCIA]", "[CONTA]", "[ENDERECO]")):
        return True
    if _is_header_fragment(value):
        return True
    if _DATE_PATTERN.search(value) or _TIME_PATTERN.search(value) or _AMOUNT_PATTERN.search(value):
        return True
    return _is_table_header(value)


def _is_public_bank_line(value: str) -> bool:
    compact_value = re.sub(r"[^A-Z0-9]+", " ", value).strip()
    for bank_name in _public_bank_names():
        if compact_value == bank_name:
            return True
        if len(bank_name.split()) >= 2 and bank_name in compact_value:
            return True
    return False


def _is_header_fragment(value: str) -> bool:
    tokens = set(re.findall(r"[A-Z]+", value))
    return bool(tokens) and tokens <= _HEADER_FRAGMENT_TERMS and bool(tokens & set(_HEADER_TERMS))


@lru_cache(maxsize=1)
def _public_bank_names() -> tuple[str, ...]:
    names: set[str] = set()
    for record in load_bank_catalog():
        for raw_name in (record.name, record.short_name):
            normalized = re.sub(r"[^A-Z0-9]+", " ", _ascii_upper(raw_name)).strip()
            if len(normalized) >= 4:
                names.add(normalized)
    return tuple(sorted(names, key=len, reverse=True))


def _ascii_upper(value: object) -> str:
    decomposed = unicodedata.normalize("NFKD", str(value or "").replace("\u00a0", " "))
    folded = "".join(character for character in decomposed if not unicodedata.combining(character))
    return " ".join(folded.upper().split())


def _safe_bbox(bbox: dict[str, float] | None) -> tuple[float, float, float, float] | None:
    if not isinstance(bbox, dict):
        return None
    values = [bbox.get(key) for key in ("left", "top", "width", "height")]
    if not all(isinstance(value, int | float) for value in values):
        return None
    left, top, width, height = (float(value) for value in values)
    if not (0 <= left <= 1 and 0 <= top <= 1 and 0 < width <= 1 - left and 0 < height <= 1 - top):
        return None
    return left, top, width, height


def _line_shape(safe_text: str) -> str:
    parts = []
    if _DATE_PATTERN.search(safe_text):
        parts.append("date")
    if _TIME_PATTERN.search(safe_text):
        parts.append("time")
    if _AMOUNT_PATTERN.search(safe_text):
        parts.append("amount")
    if "[IDENTIFICADOR]" in safe_text:
        parts.append("masked_identifier")
    return "+".join(parts) or "text"


def _semantic_role(safe_text: str) -> str:
    if "[TITULAR]" in safe_text or "[PARTE]" in safe_text:
        return "masked_party"
    if any(marker in safe_text for marker in ("[CPF]", "[CNPJ]", "[CONTA]", "[AGENCIA]", "[ENDERECO]")):
        return "masked_identifier"
    if _AMOUNT_PATTERN.search(safe_text):
        return "transaction_value"
    if _DATE_PATTERN.search(safe_text):
        return "transaction_date"
    return "layout_line"
