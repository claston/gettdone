import re
from dataclasses import dataclass

from app.application.normalization.amount import parse_amount

SIGN_TOKEN = r"[+\-\u2212]"
AMOUNT_TOKEN_PATTERN = re.compile(
    rf"(?P<amount>(?:{SIGN_TOKEN}\s*)?(?:R\$\s*)?\d+(?:\.\d{{3}})*,\d{{2}}(?:{SIGN_TOKEN})?)"
)
LOOSE_AMOUNT_PATTERN = re.compile(rf"^(?:{SIGN_TOKEN})?(?:\d{{1,3}}(?:\.\d{{3}})+|\d+)(?:[.,]\d{{2}})(?:{SIGN_TOKEN})?$")


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
    return parse_amount(raw)


def is_amount_like(raw: str) -> bool:
    value = raw.replace("\u2212", "-")
    value = re.sub(r"(?i)R\$", "", value).strip()
    return bool(LOOSE_AMOUNT_PATTERN.fullmatch(value))
