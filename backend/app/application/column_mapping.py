import re
import unicodedata

from app.application.errors import InvalidFileContentError

REQUIRED_FIELDS = {"date", "description", "amount"}

FIELD_ALIASES = {
    "date": {"date", "data", "dt", "dt_lancamento", "transaction_date", "posted_at"},
    "description": {"description", "descricao", "historico", "memo"},
    "amount": {"amount", "valor", "vlr", "valor_liquido", "value"},
    "type": {"type", "tipo", "operation_type", "natureza"},
}

_KEYWORD_HINTS = {
    "date": {"data", "date", "dt", "lancamento", "posted"},
    "description": {"descricao", "description", "historico", "memo", "hist"},
    "amount": {"valor", "amount", "vlr", "liquido", "value"},
}


def normalize_header(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.strip().lower())
    no_accents = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    cleaned = re.sub(r"[^a-z0-9]+", " ", no_accents)
    return re.sub(r"\s+", " ", cleaned).strip()


def resolve_sheet_field_map(fieldnames: list[str]) -> dict[str, str]:
    candidates: dict[str, list[tuple[int, str]]] = {}
    for canonical, aliases in FIELD_ALIASES.items():
        matches: list[tuple[int, str]] = []
        for raw_header in fieldnames:
            if not raw_header or not str(raw_header).strip():
                continue
            score = _score_header_for_field(str(raw_header), aliases, canonical)
            if score > 0:
                matches.append((score, str(raw_header)))

        if matches:
            matches.sort(key=lambda item: item[0], reverse=True)
            candidates[canonical] = matches

    field_map: dict[str, str] = {}
    for canonical, matches in candidates.items():
        best_score = matches[0][0]
        top_matches = [header for score, header in matches if score == best_score]
        if len(top_matches) > 1:
            raise InvalidFileContentError(
                f"Sheet has ambiguous column mapping for '{canonical}': {sorted(top_matches)}."
            )
        field_map[canonical] = matches[0][1]

    return field_map


def _score_header_for_field(raw_header: str, aliases: set[str], canonical: str) -> int:
    header_norm = normalize_header(raw_header)
    header_compact = header_norm.replace(" ", "")
    if not header_norm:
        return 0

    best = 0
    for alias in aliases:
        alias_norm = normalize_header(alias)
        alias_compact = alias_norm.replace(" ", "")
        if header_norm == alias_norm:
            best = max(best, 100)
            continue
        if header_compact == alias_compact:
            best = max(best, 95)
            continue
        if alias_norm and alias_norm in header_norm:
            best = max(best, 80)

    header_tokens = set(header_norm.split(" "))
    keyword_overlap = len(header_tokens & _KEYWORD_HINTS.get(canonical, set()))
    if keyword_overlap > 0:
        best = max(best, 60 + min(keyword_overlap * 5, 15))
    return best
