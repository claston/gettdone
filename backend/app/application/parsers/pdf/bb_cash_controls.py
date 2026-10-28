"""Explicit BB pending deposits are not available cash transactions."""

import re

from app.application.normalization.text import normalize_upper_text


def is_pending_deposit(text):
    normalized = normalize_upper_text(text)
    return bool(re.search(r"\b(?:DEPOSITOS? BLOQUEAD(?:O|OS)?\.?|DEPOS CHQ IMAGEM \d+ DIAS?)\b", normalized)
                and re.search(r"\d[\d.]*,\d{2}\s*\*$", normalized))
