"""Distinguish dated metadata from potentially unextracted bank movements."""
import re
from collections import Counter

from app.application.normalization.text import normalize_upper_text

_DATE = re.compile(r"^\s*\d{2}/\d{2}(?:/\d{2,4})?\b")
_DATE_VALUE = re.compile(r"\d{2}/\d{2}(?:/\d{2,4})?(?:\s+\d{2}:\d{2}(?::\d{2})?)?")
_MONEY = re.compile(r"[+-]?(?:\d[\d.]*,\d{2})[+-]?")
_FISCAL_DATE_LABEL = re.compile(
    r"^(?:COMPETENCIA D[AO] NFS-?E|DATA E HORA D[AE] EMISSAO D[AO] (?:NFS-?E|DPS))$"
)


def possible_movement_dates(text, page=None):
    """Only discount individual dates supported by positioned source evidence.

    Never trust an annex classification to exempt an entire page: that page can
    also contain a headerless bank continuation. Missing geometry stays strict.
    """
    ignored = Counter()
    if page is not None:
        lines = [line for line in page.lines if line.bbox]
        for line in lines:
            value = line.text.strip()
            if re.fullmatch(r"(?:0[1-9]|1[0-2])/\d{2}\s+R\$", value) and _labelled_value(
                line, lines, lambda label: label == "SALDO EM"
            ):
                ignored[value] += 1
                continue
            if not _DATE_VALUE.fullmatch(value):
                continue
            if _fiscal_metadata(line, lines) or _automatic_detail(line, lines):
                ignored[value] += 1
    candidates = []
    for line in text.splitlines():
        value = line.strip()
        if not _DATE.match(value):
            continue
        if ignored[value]:
            ignored[value] -= 1
        else:
            candidates.append(value)
    return candidates


def _fiscal_metadata(value, lines):
    if not any("NFS" in normalize_upper_text(line.text) for line in lines):
        return False
    return _labelled_value(value, lines, _FISCAL_DATE_LABEL.fullmatch)


def _labelled_value(value, lines, matches):
    box = value.bbox
    # The field value must sit immediately below its own label, in the same
    # column; a fiscal label elsewhere cannot exempt an unrelated bank date.
    return any(
        matches(normalize_upper_text(label.text))
        and 0 < box["top"] - label.bbox["top"] <= .025
        and abs(box["left"] - label.bbox["left"]) <= .025
        for label in lines
    )


def _automatic_detail(value, lines):
    """Numeric automatic-investment detail after an explicitly closed ledger.

    It is a separate explanatory table, not an extra current-account movement.
    Do not suppress free-form descriptions or investment ledgers without this
    explicit scope and closure evidence.
    """
    labels = [(normalize_upper_text(line.text), line.bbox["top"]) for line in lines]
    headings = [top for label, top in labels if label == "CONTA CORRENTE APLICACOES AUTOMATICAS"]
    if not headings:
        return False
    start = min(headings)
    end = min((top for label, top in labels if top > start and label.startswith("CONTA CORRENTE")), default=1.0)
    if not start < value.bbox["top"] < end:
        return False
    if not (any(label == "SALDO FINAL" and top < start for label, top in labels)
            and any(label == "TOTALIZADOR DE APLICACOES AUTOMATICAS" and top < start for label, top in labels)
            and any("SALDO DE PRINCIPAL" in label and start < top < value.bbox["top"] for label, top in labels)
            and any("RENDIMENTO" in label and start < top < value.bbox["top"] for label, top in labels)
            and any(label.startswith("MOVIMENTACAO APLICACOES/RESGATES")
                    and start < top < value.bbox["top"] for label, top in labels)):
        return False
    if not re.fullmatch(r"\d{2}/\d{2}", value.text.strip()):
        return False
    # Every cell on the dated row must be numeric (plus harmless table labels).
    # A movement description in that row keeps the date under coverage checks.
    tolerance = max(.003, value.bbox.get("height", .008) * .6)
    row = [line.text.strip() for line in lines
           if abs(line.bbox["top"] - value.bbox["top"]) <= tolerance
           and line.bbox["left"] >= value.bbox["left"] - .02]
    return sum(bool(_MONEY.fullmatch(cell)) for cell in row) >= 3 and all(
        _MONEY.fullmatch(cell) or _DATE_VALUE.fullmatch(cell)
        or normalize_upper_text(cell) in {"DATA", "TOTAL"} for cell in row
    )
