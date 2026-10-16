import re
import unicodedata


def normalize_upper_text(value: object) -> str:
    """Return uppercase, accent-folded text while preserving punctuation."""
    upper = unicodedata.normalize("NFKD", str(value).upper())
    without_accents = "".join(ch for ch in upper if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", without_accents).strip()


def normalize_description_text(value: object) -> str:
    """Return the canonical transaction description used for matching/export."""
    folded = normalize_upper_text(value)
    alnum_spaced = re.sub(r"[^A-Z0-9]+", " ", folded)
    return " ".join(alnum_spaced.split())


def split_credit_card_reference(value: str) -> tuple[str, str]:
    """Separate the explicit trailing card reference attached by invoice parsers."""
    normalized = normalize_upper_text(value)
    match = re.search(r"(?:^|\s)(CARTAO FINAL \d{4})$", normalized)
    if match is None:
        return normalized, ""
    return normalized[: match.start()].strip(), match[1]
