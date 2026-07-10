import re
from dataclasses import dataclass

from app.application.normalization.amount import parse_amount

SIGN_TOKEN = r"[+\-\u2212]"
CURRENCY_TOKEN = r"(?:(?:US|R)\$)"
BR_GROUPED_NUMBER_TOKEN = r"\d{1,3}(?:\.\d{3})+,\d{2}"
INTERNATIONAL_GROUPED_NUMBER_TOKEN = r"\d{1,3}(?:,\d{3})+\.\d{2}"
SPACED_GROUPED_NUMBER_TOKEN = r"\d{1,3}(?:[ \u00a0]\d{3})+[,.]\d{2}"
UNGROUPED_DECIMAL_NUMBER_TOKEN = r"\d+[,.]\d{2}"
NUMBER_TOKEN = (
    rf"(?:{BR_GROUPED_NUMBER_TOKEN}|{INTERNATIONAL_GROUPED_NUMBER_TOKEN}|"
    rf"{SPACED_GROUPED_NUMBER_TOKEN}|{UNGROUPED_DECIMAL_NUMBER_TOKEN})"
)
AMOUNT_PREFIX_TOKEN = rf"(?:(?:{SIGN_TOKEN}\s*)?(?:{CURRENCY_TOKEN}\s*)?|{CURRENCY_TOKEN}\s*{SIGN_TOKEN}\s*)"
AMOUNT_SUFFIX_TOKEN = rf"(?:{SIGN_TOKEN}|\s+{SIGN_TOKEN}(?!\s*\d)|\s*[CD])?"
AMOUNT_VALUE_TOKEN = rf"\(?{AMOUNT_PREFIX_TOKEN}{NUMBER_TOKEN}{AMOUNT_SUFFIX_TOKEN}\)?"
AMOUNT_TOKEN_PATTERN = re.compile(
    rf"(?<![\d,.])(?P<amount>{AMOUNT_VALUE_TOKEN})(?![\d,.])",
    flags=re.IGNORECASE,
)
LOOSE_AMOUNT_PATTERN = re.compile(rf"^{AMOUNT_VALUE_TOKEN}$", flags=re.IGNORECASE)


@dataclass(frozen=True)
class AmountToken:
    value: str
    start: int
    end: int


def find_amount_tokens(text: str) -> list[AmountToken]:
    return [
        AmountToken(value=match.group("amount"), start=match.start("amount"), end=match.end("amount"))
        for match in AMOUNT_TOKEN_PATTERN.finditer(text)
    ]


def parse_pdf_amount(raw: str) -> float:
    cleaned = raw.strip()
    suffix_match = re.search(r"\s*([CD])$", cleaned, flags=re.IGNORECASE)
    suffix = suffix_match.group(1).upper() if suffix_match is not None else None
    if suffix is not None:
        cleaned = cleaned[: suffix_match.start()].rstrip()
    amount = parse_amount(cleaned)
    if suffix == "C":
        return abs(amount)
    if suffix == "D":
        return -abs(amount)
    return amount


def has_explicit_amount_sign(raw: str) -> bool:
    value = raw.strip().upper()
    if not value:
        return False
    if value.startswith("(") and value.endswith(")"):
        return True
    if value.startswith(("+", "-", "\u2212")) or value.endswith(("+", "-", "\u2212")):
        return True
    return bool(re.search(r"\s[CD]$", value))


def is_amount_like(raw: str) -> bool:
    value = raw.replace("\u2212", "-").strip()
    return bool(LOOSE_AMOUNT_PATTERN.fullmatch(value))


def contains_amount_like(raw: str) -> bool:
    return AMOUNT_TOKEN_PATTERN.search(raw) is not None
