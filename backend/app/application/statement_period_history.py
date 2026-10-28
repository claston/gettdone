"""Small, durable period evidence, independent of expiring reports and user edits."""

import re
from datetime import date
from io import BytesIO

from pypdf import PdfReader

from app.application.organization_suggestions import build_source_evidence


def normalize_statement_period(value: object) -> dict[str, str] | None:
    if not isinstance(value, dict) or value.get("basis") not in ("document_metadata", "movement_dates"):
        return None
    try:
        start, end = (date.fromisoformat(value[key]) for key in ("start_date", "end_date"))
    except (KeyError, TypeError, ValueError):
        return None
    if start > end:
        return None
    return {"start_date": start.isoformat(), "end_date": end.isoformat(), "basis": value["basis"]}


def statement_period_from_analysis(analysis) -> dict[str, str] | None:
    rows = getattr(analysis, "report_transactions", None) or analysis.preview_transactions
    evidence = build_source_evidence({
        "financial_document": getattr(analysis, "financial_document", None),
        "preview_transactions": [{"date": row.date.isoformat() if isinstance(row.date, date) else row.date} for row in rows],
    })
    return normalize_statement_period(evidence.get("period"))


def statement_period_values(value: object) -> tuple[str | None, str | None, str | None]:
    period = normalize_statement_period(value)
    return (period["start_date"], period["end_date"], period["basis"]) if period else (None, None, None)


def statement_period_from_text(text: str) -> dict[str, str] | None:
    """Only explicit labelled ranges; transaction/issue/upload dates are not coverage."""
    printed_date = r"(\d{2}[/.-]\d{2}[/.-]\d{4})"
    ranges = re.findall(r"\bper[ií]odo(?:\s+(?:de|do\s+extrato))?\s*[:\-]?\s*" + printed_date
                        + r"\s*(?:a|à|até|al|[-–])\s*" + printed_date, text[:20000], flags=re.I)
    periods = set()
    for start, end in ranges:
        try:
            parsed = [date(int(parts[2]), int(parts[1]), int(parts[0])).isoformat()
                      for value in (start, end) if (parts := re.split(r"[/.-]", value))]
        except ValueError:
            return None
        period = normalize_statement_period({"start_date": parsed[0], "end_date": parsed[1], "basis": "document_metadata"})
        if not period:
            return None
        periods.add(tuple(period.items()))
    return dict(next(iter(periods))) if len(periods) == 1 else None


def statement_period_from_failed_pdf(document, exc) -> dict[str, str] | None:
    if document.file_type != "pdf":
        return None
    extraction = getattr(exc, "_ocr_source_extraction", None)
    if extraction is not None:
        return statement_period_from_text("\n".join(line.text for page in extraction.pages[:2] for line in page.lines))
    try:
        reader = PdfReader(BytesIO(document.raw_bytes))
        text = "\n".join(page.extract_text() or "" for page in reader.pages[:2])
    except Exception:
        return None
    return statement_period_from_text(text)
