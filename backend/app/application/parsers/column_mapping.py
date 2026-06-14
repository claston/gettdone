import re
import unicodedata

from app.application.errors import InvalidFileContentError

REQUIRED_FIELDS = {"date", "description", "amount"}

FIELD_ALIASES = {
    "date": {
        "date",
        "data",
        "data_movimento",
        "data_pgto",
        "dt",
        "dt_lancamento",
        "dt_movimento",
        "transaction_date",
        "posted_at",
    },
    "description": {
        "description",
        "descricao",
        "descricao_lancamento",
        "historico",
        "historico_lanc",
        "memo",
        "narrativa",
    },
    "amount": {
        "amount",
        "value",
        "valor",
        "valor_bruto",
        "valor_liquido",
        "valor_total",
        "vlr",
        "vlr_bruto",
        "vlr_liquido",
    },
    "debit": {
        "debit",
        "debito",
        "débito",
        "valor_debito",
        "valor_debito_total",
        "vlr_debito",
        "vlr_débito",
    },
    "credit": {
        "credit",
        "credito",
        "crédito",
        "valor_credito",
        "valor_credito_total",
        "vlr_credito",
        "vlr_crédito",
    },
    "type": {"type", "tipo", "operation_type", "natureza"},
}

_KEYWORD_HINTS = {
    "date": {"data", "date", "dt", "lancamento", "movimento", "posted", "pgto"},
    "description": {"descricao", "description", "historico", "memo", "hist", "narrativa"},
    "amount": {"valor", "amount", "vlr", "liquido", "bruto", "total", "value"},
    "debit": {"debit", "debito", "débito", "saida", "outflow"},
    "credit": {"credit", "credito", "crédito", "entrada", "inflow"},
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

    if canonical == "amount":
        header_tokens = set(header_norm.split(" "))
        if header_tokens & {"debit", "debito", "débito", "credit", "credito", "crédito"}:
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
