"""Bounded SQL queries over the current canonical authority. No financial writes."""
import base64
import calendar
import json
from datetime import date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from app.application.canonical_pro import CanonicalProService
from app.application.errors import AnalysisEditConflictError, AnalysisNotFoundError
from app.application.statement_period_history import statement_period_from_parts


class FinancialCenterService:
    def __init__(self, repository):
        self.repository = repository

    def month(self, actor_id, client_id, *, month=None, accounts=(), day=None, pending=False, limit=50, cursor=None):
        repo = self.repository
        if (month is not None and (month.day != 1 or not 2000 <= month.year <= 2100)) or not 1 <= limit <= 100 or len(accounts) > 50:
            raise ValueError("Recorte inválido.")
        after_date, after_id = date.min, UUID(int=0)
        if cursor:
            try:
                raw_date, raw_id = json.loads(base64.urlsafe_b64decode(cursor))
                after_date, after_id = date.fromisoformat(raw_date), UUID(raw_id)
            except (ValueError, TypeError, UnicodeError):
                raise ValueError("Paginação inválida.") from None
        with repo._connect() as conn:
            # All queries share a snapshot. No per-movement queries or JSON ledger.
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            conn.execute("SET LOCAL statement_timeout = '10s'")
            CanonicalProService(repo, None).assert_entitled(conn, actor_id)
            client = conn.execute(f'''SELECT c.id,c.name,c.tax_id,c.organization_id
                FROM {repo.p}pro_clients c JOIN {repo.p}pro_organizations o ON o.id=c.organization_id
                JOIN {repo.p}users u ON u.id=o.owner_user_id AND u.is_active
                WHERE c.id=%s AND o.owner_user_id=%s''', (client_id, actor_id)).fetchone()
            if not client:
                raise AnalysisNotFoundError
            org = client["organization_id"]
            catalog = conn.execute(f'''SELECT id,bank_code,account_number FROM {repo.p}pro_accounts
                WHERE organization_id=%s AND client_id=%s ORDER BY id''', (org, client_id)).fetchall()
            if set(accounts) - {item["id"] for item in catalog} - {"unidentified"}:
                raise AnalysisNotFoundError
            params = {"org": org, "client": client_id, "actor": actor_id,
                      "accounts": list(accounts), "all_accounts": not accounts}
            accessible = f'''SELECT a.*, j.batch_id, j.document->>'filename' AS filename,
                    LEAST(a.expires_at,j.expires_at,b.expires_at) AS access_expires_at
                FROM {repo.p}canonical_analyses a
                JOIN {repo.p}conversion_jobs j ON j.job_id=a.job_id AND j.result_analysis_id=a.analysis_id
                JOIN {repo.p}conversion_batches b ON b.batch_id=j.batch_id
                JOIN {repo.p}pro_conversion_contexts x ON x.job_id=a.job_id AND x.organization_id=a.organization_id
                WHERE a.organization_id=%(org)s AND a.client_id=%(client)s AND x.client_id=a.client_id
                    AND j.status='completed' AND b.identity_type='user' AND b.identity_id=%(actor)s
                    AND j.identity->>'identity_type'='user' AND j.identity->>'identity_id'=%(actor)s
                    AND LEAST(a.expires_at,j.expires_at,b.expires_at)>clock_timestamp()'''
            # A suspended source is explicit; never claim the remaining subset is complete.
            visibility = conn.execute(f'''WITH authorized AS ({accessible}) SELECT
                count(*) FILTER (WHERE NOT read_enabled OR NOT ui_enabled) AS unavailable,
                min(access_expires_at) FILTER (WHERE read_enabled AND ui_enabled) AS expires_at
                FROM authorized''', params).fetchone()
            source_state_sql = f'''WITH authorized AS ({accessible}) SELECT analysis_id,version,read_enabled,ui_enabled
                FROM authorized ORDER BY analysis_id'''
            source_state = conn.execute(source_state_sql, params).fetchall()
            if month is None:
                latest = conn.execute(f'''WITH authorized AS ({accessible})
                    SELECT max(m.movement_date) AS latest_date
                    FROM {repo.p}bank_movements m JOIN authorized a ON a.analysis_id=m.analysis_id
                    WHERE a.read_enabled AND a.ui_enabled AND NOT m.is_deleted
                        AND (%(all_accounts)s OR COALESCE(m.account_id,'unidentified')=ANY(%(accounts)s))''', params).fetchone()
                reference = latest["latest_date"] or datetime.now(ZoneInfo("America/Sao_Paulo")).date()
                month = reference.replace(day=1)
            length = calendar.monthrange(month.year, month.month)[1]
            if day is not None and not 1 <= day <= length:
                raise ValueError("Dia inválido.")
            params.update(start=month, end=month + timedelta(days=length))
            inactive = conn.execute(f'''WITH authorized AS ({accessible})
                SELECT a.metadata->'document_parts' AS parts,r.source_json
                FROM authorized a JOIN {repo.p}pro_received_statements r
                    ON r.job_id=a.job_id AND r.organization_id=a.organization_id
                WHERE a.read_enabled AND a.ui_enabled
                    AND a.metadata->'pdf_processing_metrics'->>'statement_no_activity' IN ('true','1')
                    AND COALESCE(a.metadata->'pdf_processing_metrics'->>'partial_statement','false') NOT IN ('true','1')
                    AND NOT EXISTS (SELECT 1 FROM {repo.p}bank_movements m
                        WHERE m.analysis_id=a.analysis_id AND NOT m.is_deleted)
                LIMIT 1000''', params).fetchall()
            inactive_days = {}
            for source in inactive:
                period = statement_period_from_parts(source["parts"])
                evidence = json.loads(source["source_json"])
                linked = set(evidence.get("account_links", {}).values())
                if not period or len(linked) != 1 or len(evidence.get("accounts") or [evidence]) != 1:
                    continue
                for index in range(length):
                    current = month + timedelta(days=index)
                    if period["start_date"] <= current.isoformat() <= period["end_date"]:
                        inactive_days.setdefault(current, set()).update(linked)
            required_accounts = set(accounts) if accounts else {a["id"] for a in catalog}
            cte = f'''WITH authorized AS ({accessible}), monthly AS (
                SELECT m.id,m.analysis_id,m.account_id,m.movement_date,m.amount,m.currency,
                    m.description,m.version,m.review_state,m.provenance->'source_page' AS source_page,
                    a.job_id,a.batch_id,a.filename,a.version AS analysis_version
                FROM {repo.p}bank_movements m JOIN authorized a ON a.analysis_id=m.analysis_id
                WHERE a.read_enabled AND a.ui_enabled AND NOT m.is_deleted
                    AND m.movement_date>=%(start)s AND m.movement_date<%(end)s
                    AND (%(all_accounts)s OR COALESCE(m.account_id,'unidentified')=ANY(%(accounts)s))
            ), collisions AS (
                SELECT account_id,movement_date FROM monthly
                GROUP BY account_id,movement_date HAVING count(DISTINCT analysis_id)>1
            ), scoped AS (
                SELECT m.*, c.movement_date IS NOT NULL AS possible_duplicate,
                    (m.review_state<>'reviewed' OR m.account_id IS NULL OR c.movement_date IS NOT NULL) AS pending
                FROM monthly m LEFT JOIN collisions c ON c.account_id IS NOT DISTINCT FROM m.account_id
                    AND c.movement_date=m.movement_date
            )'''
            summary = conn.execute(cte + ''' SELECT movement_date,count(*) AS count,
                count(*) FILTER (WHERE pending) AS pending_count, bool_or(possible_duplicate) AS duplicates,
                count(DISTINCT currency)>1 AS mixed_currency, min(currency) AS currency,
                COALESCE(sum(amount) FILTER (WHERE amount>0),0) AS inflows,
                COALESCE(-sum(amount) FILTER (WHERE amount<0),0) AS outflows
                FROM scoped GROUP BY movement_date ORDER BY movement_date''', params).fetchall()
            params.update(day=date(month.year, month.month, day) if day else None, pending=pending,
                          after_date=after_date, after_id=after_id, limit=limit+1)
            rows = conn.execute(cte + ''' SELECT * FROM scoped
                WHERE (%(day)s::date IS NULL OR movement_date=%(day)s) AND (NOT %(pending)s OR pending)
                    AND (movement_date,id)>(%(after_date)s,%(after_id)s)
                ORDER BY movement_date,id LIMIT %(limit)s''', params).fetchall()
            # Metadata counts only for still-authorized source documents that have
            # not adopted SQL. We never expose their legacy financial payload here.
            legacy = conn.execute(f'''SELECT count(*) AS count FROM {repo.p}pro_conversion_contexts x
                JOIN {repo.p}conversion_jobs j ON j.job_id=x.job_id
                JOIN {repo.p}conversion_batches b ON b.batch_id=j.batch_id
                WHERE x.organization_id=%s AND x.client_id=%s AND b.identity_id=%s AND b.identity_type='user'
                    AND j.status='completed' AND LEAST(j.expires_at,b.expires_at)>clock_timestamp()
                    AND COALESCE(j.result_payload->>'authority','')<>'postgres_v1' ''', (org, client_id, actor_id)).fetchone()
            # Expiration can elapse during the SQL work, even in a stable snapshot.
            if visibility["expires_at"] and conn.execute("SELECT clock_timestamp() AS now").fetchone()["now"] >= visibility["expires_at"]:
                raise AnalysisEditConflictError
            # A fresh snapshot catches revocation, expiry or suspension that
            # committed while we were aggregating. Never fall back to cached rows.
            with repo._connect() as check:
                CanonicalProService(repo, None).assert_entitled(check, actor_id)
                active = check.execute(f'''SELECT 1 FROM {repo.p}users u JOIN {repo.p}pro_organizations o
                    ON o.owner_user_id=u.id WHERE u.id=%s AND u.is_active AND o.id=%s''', (actor_id, org)).fetchone()
                if not active:
                    raise AnalysisNotFoundError
                if check.execute(source_state_sql, params).fetchall() != source_state:
                    raise AnalysisEditConflictError
            days_by_date = {item["movement_date"]: item for item in summary}
            days = []
            for index in range(length):
                current = month + timedelta(days=index)
                item = days_by_date.get(current)
                uncertain = bool(item and (item["duplicates"] or item["mixed_currency"]))
                no_activity = (not item and bool(required_accounts)
                               and required_accounts <= inactive_days.get(current, set())
                               and not visibility["unavailable"] and not legacy["count"])
                days.append({"date": current.isoformat(), "count": item["count"] if item else 0,
                             "pending_count": item["pending_count"] if item else 0,
                             "coverage": "no_activity_confirmed" if no_activity else "unknown",
                             "currency": item["currency"] if item and not item["mixed_currency"] else None,
                             "inflows": str(item["inflows"]) if item and not uncertain else None,
                             "outflows": str(item["outflows"]) if item and not uncertain else None,
                             "consolidation": "review_required" if uncertain else "observed"})
            next_cursor = None
            if len(rows) > limit:
                last = rows[limit-1]
                next_cursor = base64.urlsafe_b64encode(json.dumps([last["movement_date"].isoformat(), str(last["id"])]).encode()).decode()
            return {"client": {"id": client_id, "name": client["name"],
                               "tax_id": "••••" + (client["tax_id"] or "")[-4:] if client["tax_id"] else None},
                    "month": month.strftime("%Y-%m"), "accounts": [
                        {"id": a["id"], "label": f'{a["bank_code"] or "Banco não informado"} · Conta ••{(a["account_number"] or "")[-4:]}'}
                        for a in catalog] + [{"id": "unidentified", "label": "Conta a confirmar"}],
                    "days": days, "movements": [self._movement(row) for row in rows[:limit]],
                    "next_cursor": next_cursor, "coverage": "unknown", "financial_reconciliation": "not_implemented",
                    "unavailable_sources": visibility["unavailable"], "legacy_sources": legacy["count"],
                    "access_expires_at": visibility["expires_at"].isoformat() if visibility["expires_at"] else None}

    @staticmethod
    def _movement(row):
        return {"id": str(row["id"]), "date": row["movement_date"].isoformat(), "amount": str(row["amount"]),
                "currency": row["currency"], "description": row["description"], "account_id": row["account_id"],
                "version": row["version"], "review_state": row["review_state"], "pending": row["pending"],
                "possible_duplicate": row["possible_duplicate"],
                "source": {"analysis_id": row["analysis_id"], "job_id": row["job_id"], "batch_id": row["batch_id"],
                           "filename": row["filename"], "page": row["source_page"],
                           "analysis_version": row["analysis_version"], "availability": "available"}}
