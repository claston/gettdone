"""Reject missing physical pages only for explicit, identified statement counters."""

import re

from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.verified_statements import fail


def validate_statement_completeness(pages):
    text = upper("\n".join(pages))
    santander = "SANTANDER" in text and "EXTRATO CONSOLIDADO INTELIGENTE" in text
    bradesco = "BRADESCO" in text and "EXTRATO MENSAL" in text
    if not (santander or bradesco):
        return
    pattern = r"\bPAGINA\s*:\s*(\d+)\s*(?:/|DE)\s*(\d+)\b"
    counters = [re.findall(pattern, upper(page)) for page in pages]
    found = [counter for matches in counters for counter in matches]
    if not found:
        return
    totals = {int(total) for _, total in found}
    expected = [[(str(i), str(len(pages)))] for i in range(1, len(pages) + 1)]
    if totals != {len(pages)} or counters != expected:
        fail("Há páginas faltantes, repetidas ou fora de ordem neste extrato. Envie o documento original completo.")
