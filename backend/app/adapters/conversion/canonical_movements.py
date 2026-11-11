"""PostgreSQL authority for selected new PRO analyses; never used as a Free fallback.

Lock order is source batch/job/organization, context, analysis. PostgreSQL locks
serialize independent processes; the aggregate version provides explicit CAS.
No filesystem/network side effects occur in the financial transaction.
"""
from __future__ import annotations

import hashlib
import json
import re
from contextlib import contextmanager, nullcontext
from copy import deepcopy
from datetime import date, datetime, timedelta
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.application.canonical_identity import accounts_for_rows
from app.application.canonical_movements import CanonicalRollout, MovementScope, exact_amount
from app.application.errors import AnalysisAccessDeniedError, AnalysisEditConflictError, AnalysisNotFoundError
from app.application.review_commands import ReviewCommandService

_ROWS = {"preview_transactions", "report_transactions", "edit_operations"}
_TYPED = {"transaction_id", "date", "amount", "description", "is_deleted"}
_PROVENANCE = {"source_page", "source_line", "source_sequence", "source_subsequence", "source_regions", "statement_account"}


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


class PostgresMovementRepository:
    def __init__(self, database_url: str, *, schema="public", failure_hook=None):
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema):
            raise ValueError("Invalid database schema")
        self.database_url, self.schema = database_url, schema
        self.p = f'"{schema}".'
        self.failure_hook = failure_hook

    def _connect(self):
        return psycopg.connect(self.database_url, row_factory=dict_row)

    def _hook(self, stage):
        if self.failure_hook:
            self.failure_hook(stage)

    def _source(self, conn, scope, *, for_read=False):
        columns = "j.job_id" if for_read else "j.*"
        source = conn.execute(f"""SELECT {columns}, b.expires_at AS batch_expires_at
            FROM {self.p}conversion_jobs j JOIN {self.p}conversion_batches b ON b.batch_id = j.batch_id
            JOIN {self.p}pro_organizations o ON o.id = %s AND o.owner_user_id = b.identity_id
            JOIN {self.p}users u ON u.id = o.owner_user_id AND u.is_active
            WHERE j.job_id = %s AND j.result_analysis_id = %s AND b.identity_type = 'user' AND b.identity_id = %s
                AND j.identity->>'identity_type' = 'user' AND j.identity->>'identity_id' = %s
                AND j.status = 'completed' AND j.expires_at > clock_timestamp() AND b.expires_at > clock_timestamp()
            FOR UPDATE OF b, j, o, u""",
            (scope.organization_id, scope.job_id, scope.analysis_id, scope.actor_id, scope.actor_id)).fetchone()
        if source is None:
            raise AnalysisNotFoundError
        context = conn.execute(f"""SELECT client_id FROM {self.p}pro_conversion_contexts
            WHERE organization_id = %s AND job_id = %s FOR UPDATE""",
            (scope.organization_id, scope.job_id)).fetchone()
        # A pending client is valid, but caller cannot swap an identified client.
        if (context["client_id"] if context else None) != scope.client_id:
            raise AnalysisNotFoundError
        return source

    def _analysis(self, conn, scope, *, for_read=False):
        source = self._source(conn, scope, for_read=for_read)
        columns = "analysis_id, version, metadata, read_enabled, edit_enabled, ui_enabled" if for_read else "*"
        analysis = conn.execute(f"""SELECT {columns} FROM {self.p}canonical_analyses
            WHERE analysis_id = %s AND organization_id = %s AND job_id = %s
                AND client_id IS NOT DISTINCT FROM %s AND expires_at > clock_timestamp() FOR UPDATE""",
            (scope.analysis_id, scope.organization_id, scope.job_id, scope.client_id)).fetchone()
        if analysis is None:
            raise AnalysisNotFoundError
        return analysis, source

    def _unexpired(self, conn, scope):
        # clock_timestamp (not transaction_timestamp) catches expiry while waiting
        # for a row lock or executing a slow command. Source rows remain locked.
        valid = conn.execute(f"""SELECT 1 FROM {self.p}canonical_analyses a
            JOIN {self.p}conversion_jobs j ON j.job_id = a.job_id
            JOIN {self.p}conversion_batches b ON b.batch_id = j.batch_id
            WHERE a.analysis_id = %s AND LEAST(a.expires_at, j.expires_at, b.expires_at) > clock_timestamp()""",
            (scope.analysis_id,)).fetchone()
        if not valid:
            raise AnalysisNotFoundError

    def _movements(self, conn, analysis_id):
        return conn.execute(f"SELECT * FROM {self.p}bank_movements WHERE analysis_id = %s ORDER BY ordinal, id",
                            (analysis_id,)).fetchall()

    @staticmethod
    def _row(movement):
        return {**movement["attributes"], "transaction_id": movement["legacy_id"],
                "date": movement["movement_date"].isoformat(), "amount": float(movement["amount"]),
                "description": movement["description"], "is_deleted": movement["is_deleted"]}

    def _snapshot(self, conn, analysis):
        movements = self._movements(conn, analysis["analysis_id"])
        rows = [self._row(row) for row in movements]
        return {**deepcopy(analysis["metadata"]), "preview_transactions": rows, "report_transactions": deepcopy(rows),
                "authority": "postgres_v1", "version": analysis["version"],
                "movement_versions": {row["legacy_id"]: row["version"] for row in movements},
                "movement_ids": {row["legacy_id"]: str(row["id"]) for row in movements},
                "movement_review_states": {row["legacy_id"]: row["review_state"] for row in movements}}

    @staticmethod
    def _metadata(content):
        return {key: value for key, value in content.items()
                if key not in _ROWS | {"authority", "version", "movement_versions", "movement_ids", "movement_review_states"}}

    def read(self, scope: MovementScope):
        with self._connect() as conn:
            analysis, _ = self._analysis(conn, scope, for_read=True)
            content = self._snapshot(conn, analysis)
            self._unexpired(conn, scope)
            return content

    def authorize_source(self, scope: MovementScope):
        with self._connect() as conn:
            self._source(conn, scope, for_read=True)

    def authorize(self, scope: MovementScope):
        with self._connect() as conn:
            self._analysis(conn, scope, for_read=True)
            self._unexpired(conn, scope)

    def _insert_row(self, conn, scope, row, ordinal, currency, *, baseline, account_id=None):
        legacy_id = row.get("transaction_id")
        if not isinstance(legacy_id, str) or not legacy_id.strip():
            raise ValueError("New canonical analyses require stable transaction IDs")
        movement_date = date.fromisoformat(row["date"])
        amount = exact_amount(row["amount"])
        if account_id:
            account = conn.execute(f"""SELECT id FROM {self.p}pro_accounts
                WHERE organization_id = %s AND client_id = %s AND id = %s FOR SHARE""",
                (scope.organization_id, scope.client_id, account_id)).fetchone()
            if not account:
                raise AnalysisNotFoundError
        return conn.execute(f"""INSERT INTO {self.p}bank_movements
            (id, organization_id, analysis_id, legacy_id, client_id, account_id, movement_date, amount,
             currency, description, is_deleted, ordinal, provenance, baseline, attributes)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
            (uuid4(), scope.organization_id, scope.analysis_id, legacy_id, scope.client_id, account_id, movement_date,
             amount, currency, row["description"], bool(row.get("is_deleted")), ordinal,
             Jsonb({"origin": "extracted" if baseline else "manual", **{k: row.get(k) for k in _PROVENANCE}}),
             Jsonb(row) if baseline else None, Jsonb({k: v for k, v in row.items() if k not in _TYPED}))).fetchone()

    def ingest(self, scope: MovementScope, content: dict, *, currency: str, product: str, new_analysis: bool,
               rollout: CanonicalRollout, connection=None, resolve_accounts=False):
        if not rollout.allows(organization_id=scope.organization_id, product=product, new_analysis=new_analysis):
            raise ValueError("Canonical ingestion is disabled for this document")
        if content.get("financial_document") is not None or content.get("analysis_id") != scope.analysis_id:
            raise ValueError("Canonical ingestion requires an identified bank statement")
        if not re.fullmatch(r"[A-Z]{3}", currency):
            raise ValueError("An explicit currency is required")
        if content.get("edit_operations") or content.get("authority"):
            raise ValueError("Active legacy migration is not supported")
        rows = content.get("report_transactions", [])
        if rows != content.get("preview_transactions"):
            raise ValueError("Divergent transaction representations require review")
        if len({row.get("transaction_id") for row in rows}) != len(rows):
            raise ValueError("Transaction IDs must be unique within an analysis")
        if not rows and not (content.get("pdf_processing_metrics") or {}).get("statement_no_activity"):
            raise ValueError("Unconfirmed empty extraction")
        expiration = datetime.fromisoformat(content["expires_at"])
        if expiration.tzinfo is None:
            raise ValueError("Expiration must include timezone")
        ingestion_hash = _hash({"content": content, "currency": currency})
        with (nullcontext(connection) if connection is not None else self._connect()) as conn:
            source = self._source(conn, scope)
            existing = conn.execute(f"SELECT * FROM {self.p}canonical_analyses WHERE analysis_id = %s FOR UPDATE",
                                    (scope.analysis_id,)).fetchone()
            if existing:
                if (existing["ingestion_hash"] != ingestion_hash or existing["job_id"] != scope.job_id
                        or existing["organization_id"] != scope.organization_id):
                    raise AnalysisEditConflictError
                self._unexpired(conn, scope)
                return self._snapshot(conn, existing)
            # Ingestion accepts only the new, unreviewed source projection. Do not
            # select silently between a human-corrected job and an older JSON file.
            payload = source["result_payload"] or {}
            source_rows = (payload.get("analysis") or {}).get("preview_transactions")
            if payload.get("reviewed_at") or source_rows != content["preview_transactions"]:
                raise AnalysisEditConflictError
            for key in ("updated_at", "opening_balance", "closing_balance"):
                source_analysis = payload.get("analysis") or {}
                if key in source_analysis and source_analysis[key] != content.get(key):
                    raise AnalysisEditConflictError
            expiration = min(expiration, source["expires_at"], source["batch_expires_at"])
            baseline = deepcopy(content)
            content = deepcopy(content)
            content["expires_at"] = expiration.isoformat()
            conn.execute(f"""INSERT INTO {self.p}canonical_analyses
                (analysis_id,job_id,organization_id,client_id,currency,metadata,baseline,ingestion_hash,expires_at,projection)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (scope.analysis_id, scope.job_id, scope.organization_id, scope.client_id, currency,
                 Jsonb(self._metadata(content)), Jsonb(baseline), ingestion_hash, expiration, Jsonb(payload)))
            accounts = accounts_for_rows(self, conn, scope, rows) if resolve_accounts else [None] * len(rows)
            for ordinal, row in enumerate(rows):
                # Only documented source evidence can resolve a catalog account.
                # A manually selected document/export account is never inherited.
                self._insert_row(conn, scope, row, ordinal, currency, baseline=True, account_id=accounts[ordinal])
            analysis, _ = self._analysis(conn, scope)
            snapshot = self._snapshot(conn, analysis)
            self._project(conn, scope, snapshot, source)
            self._unexpired(conn, scope)
        return snapshot

    def _project(self, conn, scope, content, source):
        payload = deepcopy(source["result_payload"] or {})
        payload["analysis"] = content
        payload["authority"] = "postgres_v1"
        if content.get("reviewed_at"):
            payload["reviewed_at"] = content["reviewed_at"]
        else:
            payload.pop("reviewed_at", None)
        conn.execute(f"UPDATE {self.p}canonical_analyses SET projection = %s WHERE analysis_id = %s",
                     (Jsonb(payload), scope.analysis_id))
        conn.execute(f"UPDATE {self.p}conversion_jobs SET result_payload = %s WHERE job_id = %s",
                     (Jsonb(payload), scope.job_id))

    def confirm(self, scope, operation_id):
        with self._connect() as conn:
            self._analysis(conn, scope)
            receipt = conn.execute(f"""SELECT response FROM {self.p}movement_operations
                WHERE analysis_id = %s AND operation_id = %s AND actor_id = %s""",
                (scope.analysis_id, operation_id, scope.actor_id)).fetchone()
            self._unexpired(conn, scope)
            return receipt["response"] if receipt else None

    def apply(self, scope: MovementScope, edits: list[dict], *, expected_version: int | None = None, operation_id: str,
              channel: str, expected_updated_at: str | None = None, expected_movement_versions: dict | None = None,
              mark_reviewed: bool = False, require_edit_enabled: bool = False, **overrides):
        if not operation_id or len(operation_id) > 128 or not channel or len(channel) > 80:
            raise ValueError("An operation identity and source channel are required")
        if expected_version is None and not expected_updated_at:
            raise AnalysisEditConflictError
        if expected_version is not None and (
                not isinstance(expected_version, int) or isinstance(expected_version, bool) or expected_version < 1):
            raise ValueError("A positive expected version is required")
        if set(overrides) - {"opening_balance", "closing_balance", "bank_branch", "account_number", "bank_code"}:
            raise ValueError("Unsupported review option")
        if mark_reviewed and (edits or any(value is not None for value in overrides.values())):
            raise ValueError("Review confirmation must reference an unchanged version")
        for name in ("opening_balance", "closing_balance"):
            if overrides.get(name) is not None:
                exact_amount(overrides[name])
        for patch in edits:
            for name in ("credit", "debit"):
                if patch.get(name) is not None:
                    exact_amount(patch[name])
            if patch.get("date") is not None:
                date.fromisoformat(patch["date"])
        request_hash = _hash({"edits": edits, "expected_version": expected_version, "expected_updated_at": expected_updated_at,
                              "overrides": overrides, "mark_reviewed": mark_reviewed,
                              "movement_versions": expected_movement_versions, "channel": channel})
        with self._connect() as conn:
            analysis, source = self._analysis(conn, scope)
            if require_edit_enabled:
                plan = conn.execute(f'''SELECT s.id FROM {self.p}user_plan_subscriptions s
                    JOIN {self.p}plan_versions p ON p.id = s.plan_version_id
                    WHERE s.user_id=%s AND s.status='active' AND p.batch_conversion_enabled
                        AND p.code IN ('essencial','profissional','escritorio') FOR SHARE OF s,p''', (scope.actor_id,)).fetchone()
                if not plan:
                    raise AnalysisAccessDeniedError
            receipt = conn.execute(f"""SELECT * FROM {self.p}movement_operations
                WHERE analysis_id = %s AND operation_id = %s""", (scope.analysis_id, operation_id)).fetchone()
            if receipt:
                if receipt["request_hash"] != request_hash or receipt["actor_id"] != scope.actor_id:
                    raise AnalysisEditConflictError
                self._unexpired(conn, scope)
                return receipt["response"]
            if require_edit_enabled and not (analysis["read_enabled"] and analysis["edit_enabled"]):
                raise AnalysisEditConflictError
            if expected_version is not None and analysis["version"] != expected_version:
                raise AnalysisEditConflictError
            if expected_updated_at is not None and analysis["metadata"]["updated_at"] != expected_updated_at:
                raise AnalysisEditConflictError
            original = self._movements(conn, scope.analysis_id)
            by_id = {row["legacy_id"]: row for row in original}
            by_movement = {str(row["id"]): row for row in original}
            for key, version in (expected_movement_versions or {}).items():
                movement = by_movement.get(key) or by_id.get(key)
                if movement is None or movement["version"] != version:
                    raise AnalysisEditConflictError
            # Resolve UUIDs under the same lock as CAS; never use display order.
            edits = deepcopy(edits)
            for patch in edits:
                if patch.get("movement_id"):
                    movement = by_movement.get(patch.pop("movement_id"))
                    if movement is None:
                        raise ValueError("Unknown movement in this analysis")
                    if patch.get("row_id") not in (None, movement["legacy_id"]):
                        raise ValueError("Conflicting movement identities")
                    patch["row_id"] = movement["legacy_id"]
                if patch.get("action") != "insert" and patch.get("row_id") not in by_id:
                    raise ValueError("Canonical edits require stable row IDs")
            content = self._snapshot(conn, analysis)
            before_metadata = self._metadata(content)
            # Legacy timestamps remain opaque concurrency tokens for adapters;
            # advance monotonically even when calls share the same clock tick.
            now = conn.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
            previous = datetime.fromisoformat(content["updated_at"])
            now = max(now, previous + timedelta(microseconds=1))
            service = ReviewCommandService(now_provider=lambda: now)
            if mark_reviewed:
                content["updated_at"] = content["reviewed_at"] = now.isoformat()
            else:
                service.apply(content, deepcopy(edits), **overrides)
            rows = content["report_transactions"]
            financial_change = (any(patch.get("action", "update") != "annotate" for patch in edits)
                                or any(overrides.get(key) is not None for key in ("opening_balance", "closing_balance")))
            if financial_change:
                # Existing heuristic matches do not carry member versions. A
                # financial change invalidates the entire document's suggestions.
                for row in rows:
                    row["reconciliation_status"] = "unmatched"
                content["preview_transactions"] = deepcopy(rows)
                content.pop("reviewed_at", None)
                service._refresh_structured_result_content(content, preview_rows=service._parse_transaction_rows(rows),
                                                           report_rows=service._parse_transaction_rows(rows))
            for ordinal, row in enumerate(rows):
                old = by_id.get(row.get("transaction_id"))
                before = self._row(old) if old else None
                review_state = "reviewed" if mark_reviewed else "pending" if financial_change or not old else old["review_state"]
                if old is None:
                    # No extracted PDF geometry is fabricated for manual rows.
                    if not row.get("transaction_id"):
                        row["transaction_id"] = f"txn_{uuid4().hex}"
                    movement = self._insert_row(conn, scope, row, ordinal, analysis["currency"], baseline=False)
                elif before != row or old["review_state"] != review_state:
                    changed = conn.execute(f"""UPDATE {self.p}bank_movements SET movement_date = %s, amount = %s,
                        description = %s, is_deleted = %s, attributes = %s, ordinal = %s, version = version + 1,
                        review_state = %s WHERE id = %s AND version = %s RETURNING *""",
                        (date.fromisoformat(row["date"]), exact_amount(row["amount"]), row["description"],
                         bool(row.get("is_deleted")), Jsonb({k: v for k, v in row.items() if k not in _TYPED}),
                         ordinal, review_state, old["id"], old["version"])).fetchone()
                    if changed is None:
                        raise AnalysisEditConflictError
                    movement = changed
                else:
                    if old["ordinal"] != ordinal:
                        conn.execute(f"UPDATE {self.p}bank_movements SET ordinal = %s WHERE id = %s", (ordinal, old["id"]))
                    continue
                conn.execute(f"""INSERT INTO {self.p}movement_revisions
                    (analysis_id,movement_id,version,operation_id,actor_id,channel,before_value,after_value)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (scope.analysis_id, movement["id"], movement["version"], operation_id, scope.actor_id, channel,
                     Jsonb({"row": before, "review_state": old["review_state"]}) if before else None,
                     Jsonb({"row": row, "review_state": movement["review_state"]})))
            updated = conn.execute(f"""UPDATE {self.p}canonical_analyses SET version = version + 1, metadata = %s
                WHERE analysis_id = %s AND version = %s RETURNING *""",
                (Jsonb(self._metadata(content)), scope.analysis_id, analysis["version"])).fetchone()
            if updated is None:
                raise AnalysisEditConflictError
            snapshot = self._snapshot(conn, updated)
            self._project(conn, scope, snapshot, source)
            response = {**service._edit_result(scope.analysis_id, snapshot, operation_id),
                        "reviewed_at": snapshot.get("reviewed_at"),
                        "version": updated["version"], "movement_versions": snapshot["movement_versions"],
                        "movement_ids": snapshot["movement_ids"], "authority": "postgres_v1"}
            conn.execute(f"""INSERT INTO {self.p}movement_operations
                (analysis_id,operation_id,request_hash,actor_id,channel,version,response,metadata_change)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                (scope.analysis_id, operation_id, request_hash, scope.actor_id, channel, updated["version"],
                 Jsonb(response), Jsonb({"before": before_metadata, "after": self._metadata(content)})))
            self._hook("before_commit")
            self._unexpired(conn, scope)
        self._hook("after_commit")
        return response

    def artifact(self, scope, *, version=None):
        with self._connect() as conn:
            analysis, _ = self._analysis(conn, scope)
            if version is not None and version != analysis["version"]:
                raise AnalysisEditConflictError
            row = conn.execute(f"SELECT manifest FROM {self.p}movement_artifacts WHERE analysis_id = %s AND version = %s",
                               (scope.analysis_id, analysis["version"])).fetchone()
            self._unexpired(conn, scope)
            return row["manifest"] if row else None

    @contextmanager
    def artifact_io(self, analysis_id, *, cleanup=False):
        """Session lock only: no financial transaction spans object storage I/O.

        Expiry cleanup waits for in-flight attempts, including failed/unpublished
        ones. A later generator must reauthorize after acquiring this lock.
        """
        suffix = "" if cleanup else "_shared"
        key = f"{self.schema}:canonical-artifacts:{analysis_id}"
        with psycopg.connect(self.database_url, autocommit=True) as conn:
            conn.execute(f"SELECT pg_advisory_lock{suffix}(hashtextextended(%s, 0))", (key,))
            try:
                yield
            finally:
                conn.execute(f"SELECT pg_advisory_unlock{suffix}(hashtextextended(%s, 0))", (key,))

    def publish_artifact(self, scope, version, manifest):
        with self._connect() as conn:
            analysis, _ = self._analysis(conn, scope)
            if version != analysis["version"]:
                raise AnalysisEditConflictError
            if manifest.get("version") != version or manifest.get("expires_at") != analysis["metadata"]["expires_at"]:
                raise ValueError("Artifact manifest scope mismatch")
            for name, entry in manifest.get("files", {}).items():
                if (name not in {"converted.ofx", "converted.csv", "converted.xlsx", "report.xlsx"}
                        or not entry["key"].startswith(f"canonical/{scope.analysis_id}/v{version}/")
                        or not re.fullmatch(r"[a-f0-9]{64}", entry["sha256"])):
                    raise ValueError("Artifact manifest scope mismatch")
            conn.execute(f"""INSERT INTO {self.p}movement_artifacts (analysis_id,version,manifest) VALUES (%s,%s,%s)
                ON CONFLICT (analysis_id,version) DO NOTHING""", (scope.analysis_id, version, Jsonb(manifest)))
            self._unexpired(conn, scope)
        return self.artifact(scope, version=version)

    def purge_expired(self, *, delete_artifacts):
        # Delete the full analysis prefix, including unpublished failed attempts.
        # External deletion happens first and is retryable; SQL financial data is
        # already inaccessible. Never claim atomicity with an object store.
        with self._connect() as conn:
            expired = conn.execute(f"""SELECT a.analysis_id FROM {self.p}canonical_analyses a
                JOIN {self.p}conversion_jobs j ON j.job_id = a.job_id
                JOIN {self.p}conversion_batches b ON b.batch_id = j.batch_id
                WHERE LEAST(a.expires_at,j.expires_at,b.expires_at) <= clock_timestamp()
                UNION SELECT analysis_id FROM {self.p}canonical_artifact_cleanup""").fetchall()
        count = 0
        for item in expired:
            with self.artifact_io(item["analysis_id"], cleanup=True):
                delete_artifacts(item["analysis_id"])
                with self._connect() as conn:
                    # Match command lock order; never hold the analysis row
                    # while waiting for a job held by an in-flight command.
                    conn.execute(f"""SELECT j.job_id FROM {self.p}conversion_jobs j
                        JOIN {self.p}conversion_batches b ON b.batch_id = j.batch_id
                        JOIN {self.p}canonical_analyses a ON a.job_id = j.job_id
                        WHERE a.analysis_id = %s FOR UPDATE OF b, j""", (item["analysis_id"],)).fetchall()
                    row = conn.execute(f"""DELETE FROM {self.p}canonical_analyses WHERE analysis_id = %s
                        RETURNING job_id""", (item["analysis_id"],)).fetchone()
                    if row:
                        conn.execute(f"""UPDATE {self.p}conversion_jobs SET result_payload = NULL, status = 'expired'
                            WHERE job_id = %s""", (row["job_id"],))
                    conn.execute(f"DELETE FROM {self.p}canonical_artifact_cleanup WHERE analysis_id = %s", (item["analysis_id"],))
                    count += 1
        return count
