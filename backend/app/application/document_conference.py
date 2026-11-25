"""Explicit document-to-movement references, with current-source and TTL checks.

These links allocate no money, infer no matching, and do not change either
manual document verification or bank/fiscal reconciliation.
"""
import json
from datetime import datetime, timezone
from uuid import uuid4

from app.application.errors import AnalysisEditConflictError, AnalysisNotFoundError
from app.application.office_documents import fingerprint
from app.application.pro_fiscal_service import ProFiscalService


class DocumentConferenceService(ProFiscalService):
    def __init__(self, repository, *, current_document=None):
        super().__init__(repository)
        self.current_document = current_document

    def _job(self, conn, client, job_id):
        row = conn.execute(
            f"""SELECT j.job_id,j.batch_id,j.document,j.result_payload,j.updated_at,x.version AS context_version,
                LEAST(j.expires_at,b.expires_at) AS expires_at
            FROM {self.repo.p}conversion_jobs j JOIN {self.repo.p}conversion_batches b ON b.batch_id=j.batch_id
            JOIN {self.repo.p}pro_conversion_contexts x ON x.job_id=j.job_id
            WHERE j.job_id=%s AND x.organization_id=%s AND x.client_id=%s AND j.status='completed'
              AND j.identity->>'identity_type'='user' AND j.identity->>'identity_id'=%s
              AND b.identity_type='user' AND b.identity_id=%s
              AND LEAST(j.expires_at,b.expires_at)>clock_timestamp()""",
            (job_id, client["organization_id"], client["id"], client["owner_user_id"], client["owner_user_id"]),
        ).fetchone()
        if not row:
            raise AnalysisNotFoundError
        if self.current_document is not None:
            # The editor may have committed a legacy revision before its batch
            # projection. Compare the same authorized authority as the preview.
            current = self.current_document(client["owner_user_id"], job_id)
            row["result_payload"] = current.result.payload if current.result else {}
            row["expires_at"] = min(row["expires_at"], current.expires_at)
        analysis = (row["result_payload"] or {}).get("analysis") or {}
        if analysis.get("expires_at"):
            row["expires_at"] = min(row["expires_at"], datetime.fromisoformat(analysis["expires_at"]))
        if row["expires_at"] <= datetime.now(timezone.utc):
            raise AnalysisNotFoundError
        if any(not tx.get("is_deleted") for tx in analysis.get("preview_transactions", [])):
            raise ValueError("O extrato já é a origem do movimento. Selecione uma nota ou um comprovante para relacionar.")
        kind = analysis.get("document_type")
        if kind not in {"bank_receipt", "fiscal_invoice", "support_document"}:
            raise ValueError("Confira o tipo do documento antes de relacioná-lo ao movimento.")
        row["source"] = fingerprint([row["result_payload"], row["document"], row["context_version"], row["updated_at"]])
        row["analysis"] = analysis
        return row

    @staticmethod
    def _evidence(row):
        analysis = row["analysis"]
        financial = analysis.get("financial_document") or {}
        return {
            "job_id": row["job_id"], "batch_id": row["batch_id"], "filename": row["document"]["filename"],
            "document_type": analysis["document_type"], "source": row["source"],
            "expires_at": row["expires_at"].isoformat(), "observations": financial.get("observations", []),
            "notice": financial.get("notice") or "Confira o documento original antes de confirmar a relação.",
        }

    def document(self, actor, client_id, job_id):
        with self.repo._connect() as conn:
            client = self._client(conn, actor, client_id)
            return self._evidence(self._job(conn, client, job_id))

    def movement_links(self, actor, client_id, movement_id):
        with self.repo._connect() as conn:
            client = self._client(conn, actor, client_id)
            movement = self._movement(conn, client, movement_id)
            rows = conn.execute(
                f"""SELECT * FROM {self.repo.p}pro_document_movement_links
                WHERE organization_id=%s AND client_id=%s AND movement_id=%s AND state='confirmed'
                ORDER BY created_at,id LIMIT 101""", (client["organization_id"], client_id, str(movement_id)),
            ).fetchall()
            links, unavailable, expiries = [], 0, [movement["expires_at"]]
            for row in rows[:100]:
                try:
                    document = self._job(conn, client, row["job_id"])
                except (AnalysisNotFoundError, AnalysisEditConflictError, ValueError):
                    unavailable += 1
                    continue
                expires = min(document["expires_at"], datetime.fromisoformat(row["expires_at"]))
                if expires <= datetime.now(timezone.utc):
                    unavailable += 1
                    continue
                expiries.append(expires)
                links.append({"id": row["id"], "version": row["version"], "document": self._evidence(document),
                              "state": "confirmed" if row["source"] == document["source"]
                              and row["movement_version"] == movement["version"] else "review_required"})
            return {"links": links, "unavailable_links": unavailable, "partial": len(rows) > 100,
                    "access_expires_at": min(expiries).isoformat()}

    def _document_command(self, conn, client, actor, operation_id, request, execute):
        digest = fingerprint(request)
        now = datetime.now(timezone.utc).isoformat()
        prior = conn.execute(f"""SELECT * FROM {self.repo.p}pro_document_link_events
            WHERE organization_id=%s AND operation_id=%s AND expires_at>%s""",
            (client["organization_id"], operation_id, now)).fetchone()
        if prior:
            if prior["request_hash"] != digest or prior["client_id"] != client["id"]:
                raise AnalysisEditConflictError
            return json.loads(prior["result_json"])
        result, expires = execute()
        self._client(conn, actor, client["id"])
        if expires <= datetime.now(timezone.utc):
            raise AnalysisNotFoundError
        conn.execute(f"""INSERT INTO {self.repo.p}pro_document_link_events
            (organization_id,operation_id,client_id,request_hash,actor_id,result_json,expires_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (client["organization_id"], operation_id, client["id"], digest, actor, json.dumps(result), expires.isoformat()))
        return result

    def link_document(self, actor, client_id, movement_id, *, job_id, source, movement_version, operation_id):
        with self.repo._connect() as conn:
            client = self._client(conn, actor, client_id)
            movement = self._movement(conn, client, movement_id)
            document = self._job(conn, client, job_id)
            if movement["version"] != movement_version or document["source"] != source:
                raise AnalysisEditConflictError

            def execute():
                prior = conn.execute(f"""SELECT * FROM {self.repo.p}pro_document_movement_links
                    WHERE organization_id=%s AND job_id=%s AND movement_id=%s AND state='confirmed'""",
                    (client["organization_id"], job_id, str(movement_id))).fetchone()
                expiry = min(document["expires_at"], movement["expires_at"])
                if prior and prior["source"] == source and prior["movement_version"] == movement_version:
                    return {"id": prior["id"], "version": prior["version"]}, expiry
                identifier = prior["id"] if prior else str(uuid4())
                version = prior["version"] + 1 if prior else 1
                conn.execute(f"""INSERT INTO {self.repo.p}pro_document_movement_links
                    (id,organization_id,client_id,job_id,movement_id,source,movement_version,version,state,actor_id,created_at,expires_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'confirmed',%s,%s,%s)
                    ON CONFLICT(id) DO UPDATE SET source=excluded.source,movement_version=excluded.movement_version,
                    version=excluded.version,actor_id=excluded.actor_id,expires_at=excluded.expires_at""",
                    (identifier, client["organization_id"], client_id, job_id, str(movement_id), source, movement_version,
                     version, actor, datetime.now(timezone.utc).isoformat(), expiry.isoformat()))
                # An organization/source edit racing the command must not be acknowledged as current.
                if self._job(conn, client, job_id)["source"] != source:
                    raise AnalysisEditConflictError
                return {"id": identifier, "version": version}, expiry

            return self._document_command(conn, client, actor, operation_id,
                                          ["link", str(movement_id), job_id, source, movement_version], execute)

    def unlink_document(self, actor, client_id, movement_id, link_id, *, expected_version, operation_id):
        with self.repo._connect() as conn:
            client = self._client(conn, actor, client_id)
            movement = self._movement(conn, client, movement_id)

            def execute():
                row = conn.execute(f"""SELECT * FROM {self.repo.p}pro_document_movement_links
                    WHERE id=%s AND organization_id=%s AND client_id=%s AND movement_id=%s FOR UPDATE""",
                    (str(link_id), client["organization_id"], client_id, str(movement_id))).fetchone()
                if not row:
                    raise AnalysisNotFoundError
                if row["version"] != expected_version:
                    raise AnalysisEditConflictError
                conn.execute(f"UPDATE {self.repo.p}pro_document_movement_links SET state='undone',version=version+1 WHERE id=%s",
                             (row["id"],))
                return {"id": row["id"], "version": expected_version + 1}, min(
                    movement["expires_at"], datetime.fromisoformat(row["expires_at"]))

            return self._document_command(conn, client, actor, operation_id,
                                          ["unlink", str(movement_id), str(link_id), expected_version], execute)
