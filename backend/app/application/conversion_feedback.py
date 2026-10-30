"""One optional answer per owned conversion; exposures form the survey cohort."""

from typing import Literal

from app.application.conversion.identity import IdentityContext


def save_feedback(service, *, identity: IdentityContext, processing_id: str, answer: Literal["yes", "no"] | None):
    identity_type = "registered" if identity.identity_type == "user" else "anonymous"
    now = service.now_provider().isoformat()
    key = (processing_id, identity_type, identity.identity_id)
    with service._lock:
        with service._connect() as conn:
            service._execute(
                conn,
                """INSERT INTO conversion_feedback
                    (processing_id, identity_type, identity_id, offered_at, answer, answered_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(processing_id, identity_type, identity_id) DO UPDATE SET
                    answer=COALESCE(excluded.answer, conversion_feedback.answer),
                    answered_at=COALESCE(excluded.answered_at, conversion_feedback.answered_at)""",
                (*key, now, answer, now if answer else None),
            )
            row = service._fetchone(
                conn,
                """SELECT answer FROM conversion_feedback
                   WHERE processing_id=? AND identity_type=? AND identity_id=?""",
                key,
            )
            conn.commit()
    return {"processing_id": processing_id, "answer": row["answer"]}


def feedback_summary(service, conn, *, start_at: str, end_at: str, identity_type: str):
    query = "SELECT identity_type, identity_id, answer FROM conversion_feedback WHERE offered_at >= ? AND offered_at <= ?"
    params = (start_at, end_at)
    if identity_type != "all":
        query += " AND identity_type = ?"
        params += (identity_type,)
    rows = service._fetchall(conn, query, params)
    result = {"offered_count": len(rows)}
    for answer, label in (("yes", "yes"), ("no", "no"), (None, "unanswered")):
        matching = [row for row in rows if row["answer"] == answer]
        result[f"{label}_count"] = len(matching)
        result[f"{label}_people"] = len({(row["identity_type"], row["identity_id"]) for row in matching})
    return result
