"""Owner-private statement works, optimistic writes and immutable closure revisions."""

import json
from datetime import datetime, timezone
from uuid import uuid4

from app.application.pro_organization import OrganizationConflict, OrganizationService
from app.application.statement_work_assessment import assess_work, fingerprint


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


class StatementWorkService(OrganizationService):
    def __init__(self, db, repository):
        super().__init__(db)
        self.repository = repository

    def _work(self, conn, owner, work_id):
        org = self._organization(conn, owner)
        row = self.db.fetchone(conn, "SELECT * FROM pro_statement_works WHERE organization_id = ? AND id = ?", (org, work_id))
        if row is None:
            raise KeyError("Trabalho não encontrado.")
        return org, dict(row)

    def _event(self, conn, org, work_id, owner, event, detail):
        self._write(
            conn,
            "INSERT INTO pro_statement_work_events VALUES (?, ?, ?, ?, ?, ?, ?)",
            (f"evt_{uuid4().hex}", org, work_id, event, encode(detail), self._now(), owner),
        )

    def create(self, owner, client_id, period_id):
        with self.db.connect() as conn:
            org = self._organization(conn, owner, create=True)
            self._entity(conn, "pro_clients", org, client_id)
            self._entity(conn, "pro_periods", org, period_id)
            work_id, now = f"work_{uuid4().hex}", self._now()
            changed = self._write(
                conn,
                """INSERT INTO pro_statement_works
                (id,organization_id,client_id,period_id,state,version,settings,created_at,updated_at,created_by,updated_by)
                VALUES (?,?,?,?,'open',1,?,?,?,?,?) ON CONFLICT(organization_id,client_id,period_id) DO NOTHING""",
                (
                    work_id,
                    org,
                    client_id,
                    period_id,
                    encode({"account_ids": [], "evidence": {}, "exclusions": {}, "duplicate_resolutions": {}, "scope_confirmed_at": None}),
                    now,
                    now,
                    owner,
                    owner,
                ),
            )
            row = self.db.fetchone(
                conn,
                "SELECT id FROM pro_statement_works WHERE organization_id=? AND client_id=? AND period_id=?",
                (org, client_id, period_id),
            )
            if changed:
                self._event(conn, org, work_id, owner, "created", {"client_id": client_id, "period_id": period_id})
            conn.commit()
        return self.get(owner, row["id"])

    def _mutate(self, owner, work_id, version, event, change, *, completed=False):
        with self.db.connect() as conn:
            org, row = self._work(conn, owner, work_id)
            if row["version"] != version:
                raise OrganizationConflict("O trabalho mudou. Atualize a tela antes de salvar.")
            if (row["state"] == "completed") != completed:
                raise OrganizationConflict(
                    "Reabra o trabalho concluído antes de alterá-lo." if not completed else "O trabalho já está aberto."
                )
            if (
                self._write(
                    conn,
                    "UPDATE pro_statement_works SET version=version+1 WHERE organization_id=? AND id=? AND version=?",
                    (org, work_id, version),
                )
                != 1
            ):
                raise OrganizationConflict("O trabalho mudou. Atualize a tela antes de salvar.")
            settings = json.loads(row["settings"])
            state, detail = change(conn, org, row, settings)
            self._write(
                conn,
                "UPDATE pro_statement_works SET settings=?,state=?,updated_at=?,updated_by=? WHERE organization_id=? AND id=?",
                (encode(settings), state, self._now(), owner, org, work_id),
            )
            self._event(conn, org, work_id, owner, event, detail)
            conn.commit()
        return self.get(owner, work_id)

    def scope(self, owner, work_id, version, account_ids):
        def change(conn, org, row, settings):
            ids = sorted(set(account_ids))
            if not ids:
                raise ValueError("Inclua ao menos uma conta no escopo.")
            for account_id in ids:
                if self._entity(conn, "pro_accounts", org, account_id)["client_id"] != row["client_id"]:
                    raise ValueError("Todas as contas devem pertencer ao cliente do trabalho.")
            settings.update(account_ids=ids, scope_confirmed_at=self._now(), scope_confirmed_by=owner)
            return "open", {"account_ids": ids}

        return self._mutate(owner, work_id, version, "scope_confirmed", change)

    def attach(self, owner, work_id, version, job_id, account_id, context_version):
        record = self.repository.get(job_id)
        if record is None or record.job.identity.identity_type != "user" or record.job.identity.identity_id != owner:
            raise KeyError("Conversão não encontrada.")

        def change(conn, org, row, settings):
            current = self.db.fetchone(conn, "SELECT * FROM pro_conversion_contexts WHERE organization_id=? AND job_id=?", (org, job_id))
            if current and current["client_id"] not in {None, row["client_id"]}:
                raise OrganizationConflict("O arquivo pertence a outro cliente. Corrija sua organização antes de vinculá-lo.")
            periods = [
                p["period_id"]
                for p in self.db.fetchall(
                    conn, "SELECT period_id FROM pro_conversion_periods WHERE organization_id=? AND job_id=?", (org, job_id)
                )
            ]
            self.save_context_in_connection(
                conn,
                owner,
                job_id=job_id,
                client_id=row["client_id"],
                account_id=account_id,
                period_ids=[*periods, row["period_id"]],
                expected_version=context_version,
            )
            return "open", {"job_id": job_id, "account_id": account_id}

        return self._mutate(owner, work_id, version, "document_linked", change)

    def annotate(self, owner, work_id, version, kind, key, value):
        def change(conn, org, row, settings):
            evaluation = self._evaluate(conn, org, row, settings, owner)
            if kind in {"evidence", "exclusions"} and key not in {doc["job_id"] for doc in evaluation["documents"]}:
                raise KeyError("Extrato não encontrado neste trabalho.")
            if kind == "duplicate_resolutions" and key not in {group["id"] for group in evaluation["assessment"]["duplicate_groups"]}:
                raise KeyError("Grupo de duplicidade não encontrado.")
            if value is None:
                settings[kind].pop(key, None)
            else:
                settings[kind][key] = {**value, "actor": owner, "at": self._now()}
            return "open", {"kind": kind, "key": key, "value": value}

        return self._mutate(owner, work_id, version, kind, change)

    def attach_batch(self, owner, work_id, version, batch_id):
        snapshot = self.repository.get_for_owner(batch_id, identity_type="user", identity_id=owner)
        if snapshot is None:
            raise KeyError("Lote não encontrado.")

        def change(conn, org, row, settings):
            for record in snapshot.jobs:
                job_id = record.job.job_id
                current = self.db.fetchone(
                    conn, "SELECT * FROM pro_conversion_contexts WHERE organization_id=? AND job_id=?", (org, job_id)
                )
                if current and current["client_id"] not in {None, row["client_id"]}:
                    raise OrganizationConflict("Um arquivo do lote já pertence a outro cliente.")
                periods = [
                    p["period_id"]
                    for p in self.db.fetchall(
                        conn, "SELECT period_id FROM pro_conversion_periods WHERE organization_id=? AND job_id=?", (org, job_id)
                    )
                ]
                if row["period_id"] in periods and current and current["client_id"] == row["client_id"]:
                    continue
                self.save_context_in_connection(
                    conn,
                    owner,
                    job_id=job_id,
                    client_id=row["client_id"],
                    account_id=current["account_id"] if current and current["client_id"] == row["client_id"] else None,
                    period_ids=[*periods, row["period_id"]],
                    expected_version=current["version"] if current else 0,
                )
            return "open", {"batch_id": batch_id, "jobs": [r.job.job_id for r in snapshot.jobs]}

        return self._mutate(owner, work_id, version, "batch_linked", change)

    def _evaluate(self, conn, org, row, settings, owner):
        client = self._entity(conn, "pro_clients", org, row["client_id"])
        period = self._entity(conn, "pro_periods", org, row["period_id"])
        accounts = [
            dict(a)
            for a in self.db.fetchall(
                conn, "SELECT * FROM pro_accounts WHERE organization_id=? AND client_id=? ORDER BY id", (org, row["client_id"])
            )
        ]
        contexts = self.db.fetchall(
            conn,
            """SELECT c.* FROM pro_conversion_contexts c JOIN pro_conversion_periods p
            ON p.organization_id=c.organization_id AND p.job_id=c.job_id
            WHERE c.organization_id=? AND c.client_id=? AND p.period_id=? ORDER BY c.job_id LIMIT 201""",
            (org, row["client_id"], row["period_id"]),
        )
        if len(contexts) > 200:
            raise ValueError("O piloto aceita até 200 extratos por trabalho. Divida o período de trabalho.")
        documents = []
        for context in contexts:
            record = self.repository.get(context["job_id"])
            if record and (record.job.identity.identity_type != "user" or record.job.identity.identity_id != owner):
                continue
            expired = record is None or record.expires_at <= datetime.now(timezone.utc) or record.status == "expired"
            payload = record.result.payload or {} if record and record.result and not expired else {}
            documents.append(
                {
                    "job_id": context["job_id"],
                    "batch_id": record.job.batch_id if record else None,
                    "filename": record.job.document.filename if record else "Arquivo indisponível",
                    "account_id": context["account_id"],
                    "status": record.status.value if record else "expired",
                    "expired": expired,
                    "context_version": context["version"],
                    "analysis": payload.get("analysis") or {},
                    "source": payload.get("organization_source") or {},
                    "reviewed_at": payload.get("reviewed_at"),
                    "analysis_id": record.result.analysis_id if record and record.result and not expired else None,
                }
            )
        assessment = assess_work(client, period, accounts, documents, settings)
        scoped = set(settings.get("account_ids", []))
        included_documents = [
            d
            for d in documents
            if d["job_id"] not in settings.get("exclusions", {}) and (d["account_id"] is None or d["account_id"] in scoped)
        ]
        digest = fingerprint(
            {
                "client": client,
                "period": period,
                "accounts": [a for a in accounts if a["id"] in scoped],
                "documents": included_documents,
                "settings": settings,
            }
        )
        return {
            "client": self._public(client),
            "period": self._public(period),
            "accounts": [self._public(a) for a in accounts],
            "documents": documents,
            "assessment": assessment,
            "fingerprint": digest,
        }

    def get(self, owner, work_id):
        with self.db.connect() as conn:
            org, row = self._work(conn, owner, work_id)
            settings = json.loads(row["settings"])
            result = self._evaluate(conn, org, row, settings, owner)
            history = [
                dict(v)
                for v in self.db.fetchall(
                    conn,
                    "SELECT * FROM pro_statement_closings WHERE organization_id=? AND work_id=? ORDER BY revision DESC",
                    (org, work_id),
                )
            ]
            for closing in history:
                closing.pop("organization_id")
                closing["snapshot"] = json.loads(closing["snapshot"])
            events = [
                dict(e)
                for e in self.db.fetchall(
                    conn,
                    "SELECT event,detail,created_at,created_by FROM pro_statement_work_events "
                    "WHERE organization_id=? AND work_id=? ORDER BY created_at,id",
                    (org, work_id),
                )
            ]
        for event in events:
            event["detail"] = json.loads(event["detail"])
        for doc in result["documents"]:
            doc["analysis_version"] = doc["analysis"].get("updated_at")
            doc["source_period"] = doc["source"].get("period")
            doc["review_url"] = None if doc["expired"] else f"/review/{doc['batch_id']}/{doc['job_id']}?return_to=%2Fworks%2F{work_id}"
            doc["report_format"] = "xlsx" if doc["analysis"].get("financial_document") else "ofx"
            doc["report_url"] = None if not doc["analysis_id"] else f"/convert-report/{doc['analysis_id']}?format={doc['report_format']}"
            doc.pop("analysis")
            doc.pop("source")
        return {
            **self._public(row),
            "settings": settings,
            **result,
            "closings": history,
            "events": events,
            "needs_reassessment": bool(row["state"] == "completed" and history and history[0]["fingerprint"] != result["fingerprint"]),
        }

    def list(self, owner, *, client_id=None, period_id=None, state=None, offset=0):
        with self.db.connect() as conn:
            org = self._organization(conn, owner)
            rows = self.db.fetchall(
                conn,
                """SELECT id FROM pro_statement_works WHERE organization_id=?
                AND (CAST(? AS TEXT) IS NULL OR client_id=?) AND (CAST(? AS TEXT) IS NULL OR period_id=?)
                AND (CAST(? AS TEXT) IS NULL OR state=?)
                ORDER BY CASE WHEN state='open' THEN 0 ELSE 1 END,updated_at DESC,id LIMIT 21 OFFSET ?""",
                (org, client_id, client_id, period_id, period_id, state, state, offset),
            )
        items = []
        for row in rows[:20]:
            work = self.get(owner, row["id"])
            items.append(
                {key: work[key] for key in ("id", "client", "period", "state", "version", "needs_reassessment")}
                | {
                    "accounts_count": work["assessment"]["accounts_count"],
                    "documents_count": work["assessment"]["documents_count"],
                    "blockers_count": len(work["assessment"]["blockers"]),
                    "reservations_count": len(work["assessment"]["reservations"]),
                    "readiness": work["assessment"]["readiness"],
                    "conclusion": work["closings"][0]["snapshot"]["conclusion"] if work["closings"] else None,
                }
            )
        return {"items": items, "next_offset": offset + 20 if len(rows) > 20 else None}

    def complete(self, owner, work_id, version, expected_fingerprint, remarks):
        def change(conn, org, row, settings):
            result = self._evaluate(conn, org, row, settings, owner)
            if result["fingerprint"] != expected_fingerprint:
                raise OrganizationConflict("Documentos, vínculos ou cadastros mudaram. Atualize a conferência final.")
            assessment = result["assessment"]
            if assessment["blockers"]:
                raise OrganizationConflict("Resolva os bloqueios técnicos antes de concluir.")
            for reservation in assessment["reservations"]:
                if len(str(remarks.get(reservation.get("account_id") or "period", "")).strip()) < 3:
                    raise ValueError("Justifique as ressalvas de cada conta/período antes de concluir.")
            revision = self.db.fetchone(
                conn,
                "SELECT COALESCE(MAX(revision),0)+1 AS n FROM pro_statement_closings WHERE organization_id=? AND work_id=?",
                (org, work_id),
            )["n"]
            summary = {key: value for key, value in assessment.items() if key not in {"movements", "duplicate_groups"}}
            snapshot = {
                "client": result["client"],
                "period": result["period"],
                "accounts": result["accounts"],
                "scope": settings,
                "assessment": summary,
                "documents": [
                    {
                        "job_id": d["job_id"],
                        "context_version": d["context_version"],
                        "reviewed_at": d["reviewed_at"],
                        "result_fingerprint": fingerprint(d),
                    }
                    for d in result["documents"]
                ],
                "remarks": remarks,
                "conclusion": "with_reservations" if assessment["reservations"] else "complete",
            }
            self._write(
                conn,
                "INSERT INTO pro_statement_closings VALUES (?,?,?,?,?,?,?)",
                (org, work_id, revision, encode(snapshot), result["fingerprint"], self._now(), owner),
            )
            return "completed", {"revision": revision, "conclusion": snapshot["conclusion"], "remarks": remarks}

        return self._mutate(owner, work_id, version, "completed", change)

    def reopen(self, owner, work_id, version, reason):
        return self._mutate(owner, work_id, version, "reopened", lambda _c, _o, _r, _s: ("open", {"reason": reason}), completed=True)
