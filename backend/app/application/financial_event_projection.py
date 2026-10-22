"""Compatibility projections; the financial event contract remains authoritative."""
from decimal import Decimal

from app.application.models import CanonicalTransaction, NormalizedTransaction
from app.application.parsers.pdf.financial_reports import parse_financial_report_pages
from app.application.parsers.pdf.models import PdfParseResult
from app.application.pdf_layout_inference import PdfLayoutInference


def event_date(event: dict) -> str:
    dates = event["dates"]
    for role in ("transaction", "payment", "redemption", "application", "issue"):
        if role in dates:
            return dates[role]
    raise ValueError("Financial event has no transaction date.")


def event_amount(event: dict) -> float:
    return float(Decimal(event["cash_effect"] if event["cash_effect"] is not None else event["amount"]))


def parse_financial_pdf_pages(page_texts: list[str], *, native: bool = False) -> PdfParseResult | None:
    document = parse_financial_report_pages(page_texts)
    if document is None:
        return None
    return project_financial_document(document, page_texts, native=native)


def project_financial_document(document, page_texts, *, native=False):
    """Project a typed report without changing the meaning of its financial events."""
    document["representation"] = "native_text" if native else "ocr_text"
    document["extraction_method"] = document["representation"]
    events = document["events"]
    transactions, canonical = [], []
    for event in events:
        amount = event_amount(event)
        transaction_type = "non_cash" if event["cash_effect"] is None else "inflow" if amount >= 0 else "outflow"
        transactions.append(NormalizedTransaction(date=event_date(event), description=event["description"],
                                                 amount=amount, type=transaction_type))
        refs = event["source_refs"]
        canonical.append(CanonicalTransaction(
            date=event_date(event), description=event["description"], amount=amount, type=transaction_type,
            source_page=refs[0]["page"], source_line=refs[0]["line"], external_reference_id=event["reference"],
            source_parser="financial_report", source_line_refs=tuple((r["page"], r["line"]) for r in refs),
        ))
    failed = any(item["status"] == "failed" for item in document["validations"])
    return PdfParseResult(
        transactions=transactions, canonical_transactions=canonical,
        layout=PdfLayoutInference(layout_name=f"{document['document_type']}_events_v1", confidence=0.99, used_fallback=False),
        extracted_text="\n".join(page_texts), source_page_texts=tuple(page_texts), financial_document=document,
        parse_metrics={"selected_parser": "financial_report", "parser_selection_reason": "typed_financial_report",
                       "page_count": len(page_texts), "extracted_char_count": sum(map(len, page_texts)),
                       "flattened_line_count": sum(len(page.splitlines()) for page in page_texts),
                       "canonical_transactions_count": len(events),
                       "confidence_band": "low" if failed else "high",
                       "export_recommendation": "review_recommended" if failed else "",
                       "export_recommendation_reason": "financial_document_totals_differ" if failed else ""},
    )
