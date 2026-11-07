"""Parse a printed parenthesized sign without changing digits or supplying a sign."""
import re
from decimal import Decimal

_NUMBER = r"(?:\d{1,3}(?:\.\d{3})+|\d+),\d{2}"
_SIGNED = re.compile(
    rf"(?:(?P<amount>{_NUMBER})\s*(?:\(\s*)+(?P<sign>[+-])\s*\)|"
    rf"\(\s*(?P<prefix_sign>[+-])\s*\)\s*(?P<prefix_amount>{_NUMBER}))"
)


def parse_parenthesized_signed_money(text: str) -> Decimal | None:
    match = _SIGNED.fullmatch(text.strip())
    if match is None:
        return None
    amount = Decimal((match['amount'] or match['prefix_amount']).replace('.', '').replace(',', '.'))
    return -amount if (match['sign'] or match['prefix_sign']) == '-' else amount
