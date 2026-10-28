"""PDF counts by bytes, with explicit gaps when historical hashes are missing."""

import re
from datetime import datetime
from zoneinfo import ZoneInfo

from app.application.statement_period_history import normalize_statement_period

TIMEZONE = ZoneInfo("America/Sao_Paulo")
ATTENTION_LIMIT = 200


def _hash(event):
    value = str(event.get("file_sha256") or "").strip().lower()
    return value if re.fullmatch(r"[0-9a-f]{64}", value) else None


def _counts(events):
    hashed = [(event, _hash(event)) for event in events]
    return {
        "attempts": len(events),
        "success_attempts": sum(event["pdf_outcome"] in ("clean", "review") for event in events),
        "failure_attempts": sum(event["pdf_outcome"] == "failure" for event in events),
        "review_attempts": sum(event["pdf_outcome"] == "review" for event in events),
        "unique_documents": len({digest for _, digest in hashed if digest}),
        "unique_failed_documents": len({digest for event, digest in hashed if digest and event["pdf_outcome"] == "failure"}),
        "unique_review_documents": len({digest for event, digest in hashed if digest and event["pdf_outcome"] == "review"}),
        "unidentified_attempts": sum(digest is None for _, digest in hashed),
    }


def _period_label(period):
    if period is None:
        return "Não identificado"
    start, end = (period[key][:7] for key in ("start_date", "end_date"))
    def label(value):
        return f"{value[5:7]}/{value[:4]}"
    return label(start) if start == end else f"{label(start)} a {label(end)}"


def build_pdf_document_metrics(events):
    pdfs, documents, days, periods = [], {}, {}, {}
    for original in events:
        if not str(original.get("conversion_type") or "").lower().startswith("pdf"):
            continue
        status = str(original.get("status") or "").strip().casefold()
        # In-flight attempts have no quality outcome yet.
        if status in {"processando", "processing", "pending", "queued"}:
            continue
        success = status in {"sucesso", "success", "completed"}
        event = {**original, "pdf_outcome": ("clean" if original.get("quality_status") == "clean" else "review")
                 if success else "failure"}
        pdfs.append(event)
        try:
            created = datetime.fromisoformat(str(event["created_at"]).replace("Z", "+00:00"))
            local_day = created.astimezone(TIMEZONE).date().isoformat() if created.tzinfo else "unknown"
        except (ValueError, TypeError):
            local_day = "unknown"
        days.setdefault(local_day, []).append(event)
        period = normalize_statement_period(event.get("statement_period"))
        key = f"{period['start_date'][:7]}:{period['end_date'][:7]}" if period else "unknown"
        periods.setdefault(key, {"label": _period_label(period), "events": []})["events"].append(event)
        digest = _hash(event)
        if digest:
            documents.setdefault(digest, []).append(event)

    attention = []
    for digest, attempts in documents.items():
        failures = [event for event in attempts if event["pdf_outcome"] == "failure"]
        reviews = [event for event in attempts if event["pdf_outcome"] == "review"]
        if not failures and not reviews:
            continue
        latest = max(attempts, key=lambda event: str(event["created_at"]))
        evidence = {tuple(sorted(value.items())) for event in attempts
                    if (value := normalize_statement_period(event.get("statement_period")))}
        period = dict(next(iter(evidence))) if len(evidence) == 1 else None
        attention.append({
            **_counts(attempts), "file_sha256": digest, "document_reference": f"pdf_{digest[:12]}",
            "model": latest.get("model") or "Não identificado", "last_activity_at": latest["created_at"],
            "first_failure_at": min((event["created_at"] for event in failures), default=None),
            "last_failure_at": max((event["created_at"] for event in failures), default=None),
            "first_review_at": min((event["created_at"] for event in reviews), default=None),
            "last_review_at": max((event["created_at"] for event in reviews), default=None),
            "statement_period": period, "period_conflict": len(evidence) > 1,
            "last_error_code": next((event.get("error_subcode") or event.get("error_code")
                                     for event in sorted(failures, key=lambda event: str(event["created_at"]), reverse=True)), None),
        })
    attention.sort(key=lambda item: str(item["last_activity_at"]), reverse=True)
    return {
        **_counts(pdfs), "attention_total": len(attention), "attention_limit": ATTENTION_LIMIT,
        "attention": attention[:ATTENTION_LIMIT],
        "by_day": [{"date": key, **_counts(values)} for key, values in sorted(days.items(), reverse=True)],
        "by_period": [{"key": key, "label": periods[key]["label"], **_counts(periods[key]["events"])}
                      for key in sorted((key for key in periods if key != "unknown"), reverse=True)
                      + (["unknown"] if "unknown" in periods else [])],
    }
