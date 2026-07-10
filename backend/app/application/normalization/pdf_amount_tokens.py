import re
from dataclasses import dataclass
from functools import lru_cache

from app.application.normalization.amount import apply_amount_role_sign, parse_amount

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
    role_hint: str | None = None


def find_amount_tokens(text: str) -> list[AmountToken]:
    return [
        AmountToken(value=match.group("amount"), start=match.start("amount"), end=match.end("amount"))
        for match in AMOUNT_TOKEN_PATTERN.finditer(text)
    ]


def find_profile_amount_tokens(
    text: str,
    *,
    positive_patterns: tuple[str, ...] = (),
    negative_patterns: tuple[str, ...] = (),
) -> list[AmountToken]:
    declared_tokens = [
        *_find_declared_amount_tokens(text, patterns=negative_patterns, role_hint="debit"),
        *_find_declared_amount_tokens(text, patterns=positive_patterns, role_hint="credit"),
    ]
    selected_declared_tokens: list[AmountToken] = []
    for token in sorted(declared_tokens, key=lambda item: (item.start, item.end)):
        if _overlaps_any(token, selected_declared_tokens):
            continue
        selected_declared_tokens.append(token)

    generic_tokens = [token for token in find_amount_tokens(text) if not _overlaps_any(token, selected_declared_tokens)]
    return sorted([*selected_declared_tokens, *generic_tokens], key=lambda item: (item.start, item.end))


def parse_amount_token(token: AmountToken) -> float:
    return apply_amount_role_sign(parse_pdf_amount(token.value), token.role_hint)


def has_amount_token_explicit_sign(token: AmountToken) -> bool:
    return token.role_hint in {"credit", "debit"} or has_explicit_amount_sign(token.value)


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


def _find_declared_amount_tokens(
    text: str,
    *,
    patterns: tuple[str, ...],
    role_hint: str,
) -> list[AmountToken]:
    tokens: list[AmountToken] = []
    for raw_pattern in patterns:
        compiled_pattern = _compile_declared_amount_pattern(raw_pattern)
        if compiled_pattern is None:
            continue
        for match in compiled_pattern.finditer(text):
            tokens.append(
                AmountToken(
                    value=match.group("amount"),
                    start=match.start(),
                    end=match.end(),
                    role_hint=role_hint,
                )
            )
    return tokens


@lru_cache(maxsize=128)
def _compile_declared_amount_pattern(raw_pattern: str) -> re.Pattern[str] | None:
    value = raw_pattern.strip()
    if value in {"{amount}", "{amount_without_minus}"}:
        return None
    placeholders = re.findall(r"\{amount(?:_without_minus)?\}", value)
    if len(placeholders) != 1:
        return None
    before, after = re.split(r"\{amount(?:_without_minus)?\}", value, maxsplit=1)
    before_pattern = _compile_declared_amount_literal(before)
    after_pattern = _compile_declared_amount_literal(after)
    return re.compile(
        rf"(?<![\d,.]){before_pattern}(?P<amount>{AMOUNT_VALUE_TOKEN}){after_pattern}(?![\d,.])",
        flags=re.IGNORECASE,
    )


def _compile_declared_amount_literal(value: str) -> str:
    parts: list[str] = []
    for char in value:
        if char.isspace():
            if not parts or parts[-1] != r"\s+":
                parts.append(r"\s+")
        else:
            parts.append(re.escape(char))
    return "".join(parts)


def _overlaps_any(token: AmountToken, selected_tokens: list[AmountToken]) -> bool:
    return any(token.start < selected.end and selected.start < token.end for selected in selected_tokens)
