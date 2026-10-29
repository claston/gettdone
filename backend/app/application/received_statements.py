"""Document-led organization and minimal history, without reprocessing or quota mutations."""

import json
from datetime import datetime, timezone
from uuid import uuid4

from app.application.pro_organization import OrganizationConflict, OrganizationService
from app.application.statement_identity import normalized


def _utc(value):
    return datetime.fromisoformat(value).astimezone(timezone.utc) if isinstance(value, str) else value.astimezone(timezone.utc)


def technical_status(record):
    status = str(getattr(record.status, "value", record.status))
    payload = record.result.payload or {} if record.result else {}
    analysis = payload.get("analysis") or {}
    if status in ("failed", "expired"):
        return "attention"
    if status != "completed":
        return "processing"
    if payload.get("reviewed_at"):
        return "ready"
    warnings = any(row.get("warning_types") for row in analysis.get("preview_transactions", []))
    metrics = analysis.get("pdf_processing_metrics") or {}
    financial = analysis.get("financial_document") or {}
    warnings = warnings or any(check.get("status") not in ("ok", "passed", "valid") for check in financial.get("validations", []))
    return "attention" if warnings or metrics.get("partial_statement") or analysis.get("statement_balance_check") else "ready"


class ReceivedStatements(OrganizationService):
    def __init__(self, db, repository=None):
        super().__init__(db)
        self.repository = repository

    def _lock(self, conn, owner):
        organization = self._organization(conn, owner, create=True)
        # Serialize identity/account upserts with legacy context writes in this transaction.
        self._write(conn, "UPDATE pro_organizations SET created_at = created_at WHERE id = ?", (organization,))
        return organization

    def _client(self, conn, organization, owner, name, tax_id):
        if tax_id:
            existing = self.db.fetchone(conn, "SELECT id FROM pro_clients WHERE organization_id = ? AND tax_id = ?", (organization, tax_id))
            if existing:
                return existing["id"]
        client_id, now = f"client_{uuid4().hex}", self._now()
        self._write(conn, "INSERT INTO pro_clients (id, organization_id, name, tax_id, trade_name, created_by, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, '', ?, ?, ?)", (client_id, organization, name, tax_id or None, owner, now, now))
        return client_id

    def _account(self, conn, organization, owner, client_id, source):
        fields = [str(source.get(key) or "") for key in ("bank_code", "branch", "account_number")]
        if not all(fields) or fields[0] == "000":
            return None
        rows = self.db.fetchall(conn, "SELECT * FROM pro_accounts WHERE organization_id = ? AND client_id = ?", (organization, client_id))
        for row in rows:
            if [normalized(row[key]) for key in ("bank_code", "branch", "account_number")] == [normalized(v) for v in fields]:
                return row["id"]
        account_id, now = f"account_{uuid4().hex}", self._now()
        self._write(conn, "INSERT INTO pro_accounts (id, organization_id, client_id, name, bank_code, branch, account_number, "
                    "created_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (account_id, organization, client_id, "Conta de origem", *fields, owner, now, now))
        return account_id

    def _link_accounts(self, conn, organization, owner, job_id, client, source):
        for item in source.get("accounts") or [source]:
            account = self._account(conn, organization, owner, client, item)
            if account:
                self._write(conn, "INSERT INTO pro_received_statement_accounts (organization_id, job_id, account_id) "
                            "VALUES (?, ?, ?) ON CONFLICT DO NOTHING", (organization, job_id, account))

    def receive(self, record):
        job = record.job
        if job.identity.identity_type != "user" or not job.batch_id:
            return
        owner = job.identity.identity_id
        payload = (record.result.payload or {}) if record.result else {}
        source = payload.get("organization_source") or {}
        if not source:
            from app.application.statement_identity import identify_statement

            # A legacy export account may be a destination chosen after conversion.
            source = identify_statement({"financial_document": (payload.get("analysis") or {}).get("financial_document")})
        elif "confidence" not in source:
            source = {**source, "confidence": "strong" if source.get("holder_tax_id") else "name"}
        # Historical metadata deliberately excludes transaction/period/result data and raw text.
        source = {key: source[key] for key in ("holder_name", "holder_tax_id", "bank_code", "branch", "account_number",
                                              "confidence", "references", "representation", "accounts") if key in source}
        if source.get("confidence") == "missing" and not any(source.get(key) for key in ("holder_name", "holder_tax_id", "account_number")):
            source = {}
        with self.db.connect() as conn:
            organization = self._lock(conn, owner)
            self._write(conn, "INSERT INTO pro_received_statements "
                        "(organization_id, job_id, batch_id, filename, created_at, expires_at, technical_status, source_json) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (organization_id, job_id) DO UPDATE SET "
                        "technical_status = CASE WHEN ? = 'expired' THEN pro_received_statements.technical_status "
                        "ELSE excluded.technical_status END, "
                        "expires_at = CASE WHEN pro_received_statements.expires_at < excluded.expires_at "
                        "THEN pro_received_statements.expires_at ELSE excluded.expires_at END, "
                        "source_json = CASE WHEN pro_received_statements.source_json = '{}' THEN excluded.source_json "
                        "ELSE pro_received_statements.source_json END",
                        (organization, job.job_id, job.batch_id, job.document.filename, _utc(record.created_at).isoformat(),
                         _utc(record.expires_at).isoformat(), technical_status(record), json.dumps(source, ensure_ascii=False),
                         str(getattr(record.status, "value", record.status))))
            context = self.db.fetchone(conn, "SELECT job_id FROM pro_conversion_contexts WHERE organization_id = ? AND job_id = ?",
                                       (organization, job.job_id))
            tax, name = normalized(source.get("holder_tax_id")), str(source.get("holder_name") or "").strip()
            client = None
            if not context and name and tax.isdigit() and len(tax) in (11, 14) and source.get("confidence") == "strong":
                client = self._client(conn, organization, owner, name[:160], tax)
            elif not context and not tax and source.get("confidence") != "ambiguous":
                fields = [normalized(source.get(key)) for key in ("bank_code", "branch", "account_number")]
                if all(fields) and fields[0] != "000":
                    matches = {row["client_id"] for row in self.db.fetchall(conn,
                        "SELECT client_id, bank_code, branch, account_number FROM pro_accounts WHERE organization_id = ?", (organization,))
                        if [normalized(row[key]) for key in ("bank_code", "branch", "account_number")] == fields}
                    if len(matches) == 1:
                        client = next(iter(matches))
            if not context and client:
                account = self._account(conn, organization, owner, client, source)
                self.save_context_in_connection(conn, owner, job_id=job.job_id, client_id=client, account_id=account,
                                                period_ids=[], expected_version=0)
                self._link_accounts(conn, organization, owner, job.job_id, client, source)
            conn.commit()

    def sync(self, owner, cursor=None):
        if self.repository is None:
            return None
        import base64

        from app.routers.pro_organization import decode_cursor

        summaries = self.repository.list_files_for_owner(identity_type="user", identity_id=owner, limit=100, before=decode_cursor(cursor))
        for summary in summaries:
            record = self.repository.get(summary.job_id)
            if record and record.job.identity.identity_type == "user" and record.job.identity.identity_id == owner:
                self.receive(record)
        if len(summaries) == 100:
            last = summaries[-1]
            return base64.urlsafe_b64encode(json.dumps([last.created_at.isoformat(), last.job_id]).encode()).decode().rstrip("=")
        return None

    def dashboard(self, owner, *, now=None, after="", limit=30, pending_after=""):
        now = _utc(now or datetime.now(timezone.utc)).isoformat()
        with self.db.connect() as conn:
            organization = self._organization(conn, owner)
            if not organization:
                return {"totals": {"statements": 0, "ready": 0, "attention": 0}, "clients": [], "pending": [], "next_cursor": None}
            totals = self.db.fetchone(conn, "SELECT COUNT(*) AS statements, "
                "COALESCE(SUM(CASE WHEN technical_status = 'ready' THEN 1 ELSE 0 END), 0) AS ready, "
                "COALESCE(SUM(CASE WHEN technical_status = 'attention' THEN 1 ELSE 0 END), 0) AS attention "
                "FROM pro_received_statements WHERE organization_id = ?", (organization,))
            rows = self.db.fetchall(conn, "SELECT c.id, c.name, c.tax_id, c.trade_name, c.updated_at, COUNT(*) AS historical_count, "
                "SUM(CASE WHEN r.expires_at > ? THEN 1 ELSE 0 END) AS available_count, "
                "SUM(CASE WHEN r.technical_status = 'attention' THEN 1 ELSE 0 END) AS attention_count, "
                "SUM(CASE WHEN r.technical_status = 'processing' THEN 1 ELSE 0 END) AS processing_count "
                "FROM pro_received_statements r JOIN pro_conversion_contexts x "
                "ON x.organization_id = r.organization_id AND x.job_id = r.job_id "
                "JOIN pro_clients c ON c.organization_id = x.organization_id AND c.id = x.client_id "
                "WHERE r.organization_id = ? AND c.id > ? "
                "GROUP BY c.id, c.name, c.tax_id, c.trade_name, c.updated_at ORDER BY c.id LIMIT ?",
                (now, organization, after, limit + 1))
            clients = []
            for row in rows[:limit]:
                client = dict(row)
                client["expired_count"] = client["historical_count"] - client["available_count"]
                client["accounts"] = [dict(account) for account in self.db.fetchall(conn,
                    "SELECT a.id, a.bank_code, a.account_number, COUNT(*) AS statement_count "
                    "FROM pro_received_statements r JOIN pro_conversion_contexts x "
                    "ON x.organization_id = r.organization_id AND x.job_id = r.job_id "
                    "JOIN pro_accounts a ON a.organization_id = x.organization_id AND a.client_id = x.client_id "
                    "AND (a.id = x.account_id OR a.id IN (SELECT account_id FROM pro_received_statement_accounts "
                    "WHERE organization_id = x.organization_id AND job_id = x.job_id)) "
                    "WHERE r.organization_id = ? AND x.client_id = ? GROUP BY a.id, a.bank_code, a.account_number ORDER BY a.id",
                    (organization, row["id"]))]
                for account in client["accounts"]:
                    account["account_number"] = "••" + account["account_number"][-4:]
                clients.append(client)
            pending = self.db.fetchall(conn, "SELECT r.*, COALESCE(x.version, 0) AS version FROM pro_received_statements r "
                "LEFT JOIN pro_conversion_contexts x ON x.organization_id = r.organization_id AND x.job_id = r.job_id "
                "WHERE r.organization_id = ? AND x.client_id IS NULL AND r.expires_at > ? AND r.job_id > ? ORDER BY r.job_id LIMIT 51",
                (organization, now, pending_after))
            return {"totals": dict(totals), "clients": clients,
                    "pending": [{"job_id": row["job_id"], "batch_id": row["batch_id"], "filename": row["filename"],
                                 "version": row["version"], "source": json.loads(row["source_json"])} for row in pending[:50]],
                    "pending_next_cursor": pending[49]["job_id"] if len(pending) > 50 else None,
                    "next_cursor": rows[limit - 1]["id"] if len(rows) > limit else None}

    def documents(self, owner, client_id, *, now=None, after="", limit=None):
        now = _utc(now or datetime.now(timezone.utc)).isoformat()
        with self.db.connect() as conn:
            organization = self._organization(conn, owner)
            self._entity(conn, "pro_clients", organization, client_id)
            return [dict(row) for row in self.db.fetchall(conn, "SELECT r.job_id, r.batch_id, r.filename, r.expires_at "
                "FROM pro_received_statements r JOIN pro_conversion_contexts x "
                "ON x.organization_id = r.organization_id AND x.job_id = r.job_id "
                "WHERE r.organization_id = ? AND x.client_id = ? AND r.expires_at > ? AND r.job_id > ? ORDER BY r.job_id"
                + (" LIMIT ?" if limit else ""),
                (organization, client_id, now, after, *((limit,) if limit else ())))]

    def confirm(self, owner, job_id, *, expected_version, name, tax_id=None, client_id=None, now=None):
        now = _utc(now or datetime.now(timezone.utc))
        name = self._text(name)
        tax = normalized(tax_id)
        if tax and (not tax.isdigit() or len(tax) not in (11, 14)):
            raise ValueError("Confira o CPF/CNPJ do titular.")
        with self.db.connect() as conn:
            organization = self._lock(conn, owner)
            receipt = self.db.fetchone(conn, "SELECT * FROM pro_received_statements WHERE organization_id = ? AND job_id = ?",
                                       (organization, job_id))
            if not receipt:
                raise KeyError("Extrato não encontrado.")
            if _utc(receipt["expires_at"]) <= now:
                raise ValueError("O arquivo expirou.")
            source = json.loads(receipt["source_json"])
            if client_id:
                self._entity(conn, "pro_clients", organization, client_id)
            else:
                client_id = self._client(conn, organization, owner, name, tax)
            context = self.db.fetchone(conn, "SELECT version FROM pro_conversion_contexts WHERE organization_id = ? AND job_id = ?",
                                      (organization, job_id))
            if (context["version"] if context else 0) != expected_version:
                raise OrganizationConflict("A identificação mudou. Atualize antes de confirmar novamente.")
            account_id = self._account(conn, organization, owner, client_id, source)
            periods = [row["period_id"] for row in self.db.fetchall(conn,
                "SELECT period_id FROM pro_conversion_periods WHERE organization_id = ? AND job_id = ?", (organization, job_id))]
            result = self.save_context_in_connection(conn, owner, job_id=job_id, client_id=client_id, account_id=account_id,
                                                     period_ids=periods, expected_version=expected_version)
            self._write(conn, "DELETE FROM pro_received_statement_accounts WHERE organization_id = ? AND job_id = ?",
                        (organization, job_id))
            self._link_accounts(conn, organization, owner, job_id, client_id, source)
            conn.commit()
            return result

    def edit_client(self, owner, client_id, *, name, tax_id, expected_updated_at):
        name, tax = self._text(name), normalized(tax_id)
        if tax and (not tax.isdigit() or len(tax) not in (11, 14)):
            raise ValueError("Confira o CPF/CNPJ do titular.")
        with self.db.connect() as conn:
            organization = self._lock(conn, owner)
            client = self._entity(conn, "pro_clients", organization, client_id)
            if client["updated_at"] != expected_updated_at:
                raise OrganizationConflict("O cliente mudou. Atualize antes de salvar novamente.")
            duplicate = self.db.fetchone(conn, "SELECT id FROM pro_clients WHERE organization_id = ? AND tax_id = ? AND id <> ?",
                                         (organization, tax, client_id)) if tax else None
            if duplicate:
                raise ValueError("Já existe um cliente com este CPF/CNPJ. Confira a associação do documento.")
            self._write(conn, "UPDATE pro_clients SET name = ?, tax_id = ?, updated_at = ? WHERE organization_id = ? AND id = ?",
                        (name, tax or None, self._now(), organization, client_id))
            conn.commit()
            return {"id": client_id}
