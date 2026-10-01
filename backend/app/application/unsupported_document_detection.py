from __future__ import annotations

import re

from app.application.normalization.text import normalize_upper_text

BILLING_REPORT_DOCUMENT_TYPE = "billing_report"
BILLING_REPORT_USER_MESSAGE = (
    "Este arquivo é um relatório de faturamento, não um extrato bancário. "
    "Envie o extrato da conta em PDF, CSV, XLSX ou OFX."
)

_BILLING_REPORT_TITLE = "RELATORIO DE FATURAMENTO"
_MONTH_NAMES = (
    "JANEIRO",
    "FEVEREIRO",
    "MARCO",
    "ABRIL",
    "MAIO",
    "JUNHO",
    "JULHO",
    "AGOSTO",
    "SETEMBRO",
    "OUTUBRO",
    "NOVEMBRO",
    "DEZEMBRO",
)
_BILLING_REPORT_SUPPORTING_MARKERS = (
    "PERIODO",
    "TOTAIS",
    "CNPJ",
    "REGISTRO NO C.R.C",
    "ALIQUOTA",
    "IMPOSTOS",
)


def detect_unsupported_document_type(text: str) -> str | None:
    normalized_lines = tuple(
        normalized
        for raw_line in str(text or "").splitlines()
        if (normalized := normalize_upper_text(raw_line).strip())
    )
    if not normalized_lines:
        return None

    header_text = " ".join(normalized_lines[:6])
    if _BILLING_REPORT_TITLE not in header_text:
        return None

    normalized_text = "\n".join(normalized_lines)
    month_hits = sum(
        re.search(rf"(?<![A-Z0-9]){month}(?![A-Z0-9])", normalized_text) is not None
        for month in _MONTH_NAMES
    )
    supporting_hits = sum(marker in normalized_text for marker in _BILLING_REPORT_SUPPORTING_MARKERS)
    if month_hits >= 6 and supporting_hits >= 2:
        return BILLING_REPORT_DOCUMENT_TYPE
    return None
