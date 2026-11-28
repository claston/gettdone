"""Document lifetime follows paid entitlement, separately from permission to edit."""
from datetime import datetime, timedelta, timezone

from app.application.black_friday import PAID_PLAN_CODES

NO_SCHEDULED_DELETION = datetime(9999, 1, 1, tzinfo=timezone.utc)
GRACE_PERIOD = timedelta(days=30)


def retention_deadline(subscriptions, *, now=None):
    now = now or datetime.now(timezone.utc)
    deadlines = []
    for item in subscriptions:
        if item["code"] not in PAID_PLAN_CODES or not item["batch_conversion_enabled"]:
            continue
        if datetime.fromisoformat(str(item["started_at"])) > now:
            continue
        end = item.get("ended_at")
        if end:
            deadlines.append(datetime.fromisoformat(str(end)) + GRACE_PERIOD)
        elif item["status"] == "active" and item["code"] != "escritorio_documentos":
            deadlines.append(NO_SCHEDULED_DELETION)
    return max(deadlines, default=None)


def read_retention(access, owner):
    with access.db.connect() as conn:
        rows = access.db.fetchall(conn, """SELECT p.code,p.batch_conversion_enabled,s.status,s.started_at,s.ended_at
            FROM user_plan_subscriptions s JOIN plan_versions p ON p.id=s.plan_version_id WHERE s.user_id=?""", (owner,))
    return retention_deadline([dict(row) for row in rows])


def can_edit_documents(access, owner):
    # The identity cache can outlive cancellation; writes need current entitlement.
    from app.application.active_plan_reader import read_active_user_plan
    with access.db.connect() as conn:
        plan = read_active_user_plan(conn, fetchone=access.db.fetchone, user_id=owner)
    return bool(plan and plan['batch_conversion_enabled'])
