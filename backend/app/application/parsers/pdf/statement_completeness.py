"""Printed pagination is evidence for review, never an extraction veto."""

import re

from app.application.normalization.text import normalize_upper_text as upper


def validate_statement_completeness(pages) -> bool:
    """Return possible incompleteness; omitted pages may contain only annexes."""
    observed = []
    joined = upper("\n".join(pages))
    if ("SANTANDER" in joined
            and re.search(r"VOCE ALCANCOU O NUMERO MAXIMO DE CONSULTAS\b", joined)
            and "PARA VER TODOS OS LANCAMENTOS" in joined):
        return True
    bare_counter_layout = any(bank in joined for bank in ("MERCADO PAGO", "BTG PACTUAL"))
    for physical, page in enumerate(pages, 1):
        text = "\n".join(upper(line) for line in page.splitlines())
        matches = re.findall(r"\b(?:PAGINA|FOLHA)\s*:?\s*(?:DE\s*)?(\d+)\s*(?:/|DE)\s*(\d+)\b", text)
        if not matches:
            matches = re.findall(r"EXTRATO MENSAL[^\n]*?\b(\d{3})\s*[/|]\s*(\d{3})(?!\d)", text)
        if not matches:
            matches = re.findall(r"(?:/EXTRATO|REF:\S+)\s+(\d{1,3})\s*/\s*(\d{1,3})\b", text)
        if not matches:
            matches = re.findall(r"BTG PACTUAL[^\n]*?\b(\d{1,3})\s*/\s*(\d{1,3})\b", text)
        if not matches:
            digits = r"\d{1,3}" if bare_counter_layout else r"\d{3}"
            matches = re.findall(rf"(?m)^\s*({digits})\s*/\s*({digits})\s*$", text)
        if matches:
            if len(matches) != 1:
                return True
            number, total = map(int, matches[0])
            observed.append((physical, number, total))
        else:
            singles = re.findall(r"\b(?:PAGINA\s*:?|PAG\.?)\s+(\d+)\b", text)
            if singles and (len(singles) != 1 or int(singles[0]) != physical):
                return True
    return any(number != physical or total != len(pages) for physical, number, total in observed)
