from __future__ import annotations

import re
import unicodedata

from app.application.conversion.v3.layout import sanitize_semantic_line

_MASKS = frozenset(
    {
        "[AGENCIA]",
        "[CNPJ]",
        "[CONTA]",
        "[CPF]",
        "[EMAIL]",
        "[ENDERECO]",
        "[IDENTIFICADOR]",
        "[PARTE]",
        "[TELEFONE]",
        "[TITULAR]",
    }
)
_STRUCTURED_PII_PATTERNS = (
    re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b"),
    re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b"),
    re.compile(r"(?<!\d)(?:\*{3}|\d{3})\.\d{3}\.\d{3}-(?:\*{2}|\d{2})(?!\d)"),
    re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"),
    re.compile(r"\b(?:AGENCIA|CONTA(?:\s+CORRENTE)?)\s*:?\s*(?!\[(?:AGENCIA|CONTA)\])[A-Z0-9./-]+"),
)


def validate_v3_output(*, source_text: str, output_text: str) -> None:
    normalized_output = _ascii_upper(output_text)
    for pattern in _STRUCTURED_PII_PATTERNS:
        if pattern.search(normalized_output):
            raise ValueError("v3_structured_pii_detected")
    for fragment in _sensitive_source_fragments(source_text):
        if len(fragment) >= 4 and fragment in normalized_output:
            raise ValueError("v3_source_pii_leak_detected")


def validate_v3_manifest(manifest: dict[str, object], *, source_text: str) -> None:
    pages = manifest.get("pages")
    if not isinstance(pages, list):
        raise ValueError("v3_manifest_pages_invalid")
    values: list[str] = []
    for page in pages:
        if not isinstance(page, dict) or not isinstance(page.get("elements"), list):
            raise ValueError("v3_manifest_elements_invalid")
        for element in page["elements"]:
            if not isinstance(element, dict):
                raise ValueError("v3_manifest_element_invalid")
            values.append(str(element.get("text") or ""))
    validate_v3_output(source_text=source_text, output_text="\n".join(values))


def _sensitive_source_fragments(source_text: str) -> set[str]:
    fragments: set[str] = set()
    unchanged_fragments: set[str] = set()
    in_transaction_table = False
    for raw_line in source_text.splitlines():
        normalized = _ascii_upper(raw_line)
        if sum(term in normalized for term in ("DATA", "HISTORICO", "VALOR", "SALDO")) >= 3:
            in_transaction_table = True
        sanitized = sanitize_semantic_line(raw_line, in_transaction_table=in_transaction_table)
        if sanitized == normalized:
            unchanged_fragments.update(re.findall(r"[A-Z][A-Z0-9./@-]{3,}", normalized))
            continue
        for value in _changed_segments(normalized, sanitized):
            if value and value not in _MASKS:
                fragments.add(value)
    return fragments - unchanged_fragments


def _changed_segments(source: str, sanitized: str) -> set[str]:
    source_words = set(re.findall(r"[A-Z][A-Z0-9./@-]{3,}", source))
    safe_words = set(re.findall(r"[A-Z][A-Z0-9./@-]{3,}", sanitized))
    return source_words - safe_words


def _ascii_upper(value: object) -> str:
    decomposed = unicodedata.normalize("NFKD", str(value or "").replace("\u00a0", " "))
    folded = "".join(character for character in decomposed if not unicodedata.combining(character))
    return " ".join(folded.upper().split())
