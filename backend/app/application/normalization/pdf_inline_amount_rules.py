from __future__ import annotations

import re
from dataclasses import dataclass

from app.application.normalization.pdf_amount_tokens import AmountToken, find_amount_tokens


@dataclass(frozen=True)
class InlineAmountMatch:
    description: str
    amount_token: AmountToken


def extract_single_trailing_amount_match(raw_text: str) -> InlineAmountMatch | None:
    text = raw_text.strip()
    if not text:
        return None

    amount_tokens = find_amount_tokens(text)
    if len(amount_tokens) != 1:
        return None

    amount_token = amount_tokens[0]
    trailing = text[amount_token.end :].strip()
    if trailing and not _is_ignorable_trailing_noise(trailing):
        return None

    description = text[: amount_token.start].strip()
    if not description:
        return None

    return InlineAmountMatch(description=description, amount_token=amount_token)


def _is_ignorable_trailing_noise(value: str) -> bool:
    return bool(re.fullmatch(r"[|¦:;,\.\-_/\\Il!]+", value))
