"""Sicoob's explicit blocked-cheque histories, independent of text/OCR reader."""
import re

from app.application.normalization.text import normalize_upper_text

_BLOCKED_CHEQUE = re.compile(r"DEP\.?\s*(?:CHEQUE\s+|CH\.?)BLOQ\.?\s*\d+\s*D(?:-INTE)?")
_CHANNEL_CHEQUE = re.compile(r"(?:DEP|DP)\.?\s+CH\.?\s*CANAL\s+ATEN(?:D)?\.?(?:\s*\d+\s*D)?")


def is_blocked_cheque_history(history):
    # Match the primary printed history, never a phrase in a counterparty's
    # complement. Releases and C/D movements retain their explicit cash effect.
    normalized = normalize_upper_text(history)
    return bool(_BLOCKED_CHEQUE.fullmatch(normalized) or _CHANNEL_CHEQUE.fullmatch(normalized))


def is_sisbr_history_grid(text):
    compact = re.sub(r"[\s-]+", "", normalize_upper_text(text))
    return all(marker in compact for marker in
               ("SICOOB", "SISBR", "COOP.:", "HISTORICODEMOVIMENTACAO", "DATAHISTORICOVALOR"))
