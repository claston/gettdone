"""Document-led organization and minimal history, without reprocessing or quota mutations."""

import json
import logging
import unicodedata
from datetime import datetime, timezone
from io import BytesIO
from uuid import uuid4

from app.application.pro_organization import OrganizationConflict, OrganizationService
from app.application.statement_identity import (
    IDENTITY_VERSION,
    account_identity,
    account_signature,
    identify_native_statement,
    identify_statement,
    normalized,
)

logger = logging.getLogger(__name__)


def _utc(value):
    return datetime.fromisoformat(value).astimezone(timezone.utc) if isinstance(value, str) else value.astimezone(timezone.utc)


def _search_text(value):
    text = unicodedata.normalize("NFD", (value or "").strip().casefold())
    return "".join(char for char in text if not unicodedata.combining(char))


def _client_name_order(row):
    return _search_text(row["name"]), row["id"]


def _matches_client(row, query):
    text = _search_text(query)
    digits = "".join(char for char in text if char.isdecimal())
    document_query = digits and all(char.isdecimal() or char in ".-/ " for char in text)
    return (not text or text in _search_text(row["name"]) or text in _search_text(row["trade_name"])
            or bool(document_query and digits in normalized(row["tax_id"])))


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
    def __init__(self, db, repository=None, *, document_store=None):
        super().__init__(db)
        self.repository = repository
        self.document_store = document_store

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

    def _prefer_document_name(self, conn, organization, client_id, source, record):
        """Same tax identity; a later document wins over a stale catalog name."""
        tax = normalized(source.get("holder_tax_id"))
        name = str(source.get("holder_name") or "").strip()[:160]
        if (not name or source.get("confidence") != "strong" or not tax.isdigit() or len(tax) not in (11, 14)
                or str(getattr(record.status, "value", record.status)) != "completed"):
            return
        client = self.db.fetchone(conn, "SELECT name, tax_id FROM pro_clients WHERE organization_id = ? AND id = ?",
                                 (organization, client_id))
        if not client or normalized(client["tax_id"]) != tax or client["name"] == name:
            return
        if self.db.fetchone(conn, "SELECT 1 FROM pro_client_identity_confirmations WHERE organization_id = ? AND client_id = ?",
                            (organization, client_id)):
            return
        latest = self.db.fetchone(conn, "SELECT r.created_at, r.job_id FROM pro_received_statements r "
            "JOIN pro_conversion_contexts x ON x.organization_id = r.organization_id AND x.job_id = r.job_id "
            "WHERE r.organization_id = ? AND x.client_id = ? ORDER BY r.created_at DESC, r.job_id DESC LIMIT 1",
            (organization, client_id))
        current = (_utc(record.created_at).isoformat(), record.job.job_id)
        if latest and (latest["created_at"], latest["job_id"]) > current:
            return
        self._write(conn, "UPDATE pro_clients SET name = ?, updated_at = ? WHERE organization_id = ? AND id = ?",
                    (name, self._now(), organization, client_id))

    def _account(self, conn, organization, owner, client_id, source):
        fields = [str(source.get(key) or "") for key in ("bank_code", "branch", "account_number")]
        key = account_signature(source)
        if not key:
            return None
        rows = self.db.fetchall(conn, "SELECT * FROM pro_accounts WHERE organization_id = ? AND client_id = ?", (organization, client_id))
        known = {json.loads(row["source_json"]).get("account_links", {}).get(key)
                 for row in self._account_receipts(conn, organization, key) if row["client_id"] == client_id}
        for row in rows:
            if (row["id"] in known and
                    [normalized(row[key]) for key in ("bank_code", "branch", "account_number")] == [normalized(v) for v in fields]):
                return row["id"]
            if (not source.get("cooperative") and
                    [normalized(row[key]) for key in ("bank_code", "branch", "account_number")] == [normalized(v) for v in fields]):
                return row["id"]
        account_id, now = f"account_{uuid4().hex}", self._now()
        name = f"Conta de origem · Cooperativa {source['cooperative']}" if source.get("cooperative") else "Conta de origem"
        self._write(conn, "INSERT INTO pro_accounts (id, organization_id, client_id, name, bank_code, branch, account_number, "
                    "created_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (account_id, organization, client_id, name, *fields, owner, now, now))
        return account_id

    def _account_receipts(self, conn, organization, key):
        rows = self.db.fetchall(conn, "SELECT r.source_json, x.client_id FROM pro_received_statements r "
            "JOIN pro_conversion_contexts x ON x.organization_id = r.organization_id AND x.job_id = r.job_id "
            "WHERE r.organization_id = ? AND x.client_id IS NOT NULL AND r.source_json LIKE ?", (organization, f'%"{key}"%'))
        return [row for row in rows if key in self._source_keys(json.loads(row["source_json"]))]

    @staticmethod
    def _source_keys(source):
        return sorted({key for item in source.get("accounts") or [source] if (key := account_signature(item))})

    def _known_clients(self, conn, organization, source):
        key = account_signature(source)
        if not key:
            return set()
        matches = {row["client_id"] for row in self._account_receipts(conn, organization, key)}
        # Legacy accounts use genuine agency fields. A cooperative is a separate scope.
        if not source.get("cooperative"):
            for row in self.db.fetchall(conn, "SELECT * FROM pro_accounts WHERE organization_id = ?", (organization,)):
                if account_identity({"bank_code": row["bank_code"], "branch": row["branch"],
                                     "account_number": row["account_number"]}) == account_identity(source):
                    matches.add(row["client_id"])
        return matches

    def _link_accounts(self, conn, organization, owner, job_id, client, source):
        links = {}
        for item in source.get("accounts") or [source]:
            account = self._account(conn, organization, owner, client, item)
            if account:
                links[account_signature(item)] = account
                self._write(conn, "INSERT INTO pro_received_statement_accounts (organization_id, job_id, account_id) "
                            "VALUES (?, ?, ?) ON CONFLICT DO NOTHING", (organization, job_id, account))
        self._write(conn, "UPDATE pro_received_statements SET source_json = ? WHERE organization_id = ? AND job_id = ?",
                    (json.dumps({**source, "account_links": links}, ensure_ascii=False), organization, job_id))
        return links.get(account_signature(source))

    def receive(self, record, *, recovered_source=None):
        job = record.job
        if job.identity.identity_type != "user" or not job.batch_id:
            return
        owner = job.identity.identity_id
        payload = (record.result.payload or {}) if record.result else {}
        source = recovered_source if recovered_source is not None else payload.get("organization_source") or {}
        if not source:
            # A legacy export account may be a destination chosen after conversion.
            source = identify_statement({"financial_document": (payload.get("analysis") or {}).get("financial_document")})
        elif "confidence" not in source:
            source = {**source, "confidence": "strong" if source.get("holder_tax_id") else "name"}
        # Historical metadata deliberately excludes transaction/period/result data and raw text.
        source = {key: source[key] for key in ("version", "holder_name", "holder_tax_id", "bank_code", "branch", "cooperative",
                                              "account_number", "confidence", "references", "representation", "accounts",
                                              "native_identity_checked", "identity_issues") if key in source}
        if (source.get("confidence") == "missing" and not source.get("native_identity_checked")
                and not any(source.get(key) for key in ("holder_name", "holder_tax_id", "account_number"))):
            source = {}
        if source:
            source["account_keys"] = self._source_keys(source)
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
            stored = json.loads(self.db.fetchone(conn,
                "SELECT source_json FROM pro_received_statements WHERE organization_id = ? AND job_id = ?",
                (organization, job.job_id))["source_json"])
            if source.get("version", 0) > stored.get("version", 0):
                self._write(conn, "UPDATE pro_received_statements SET source_json = ? WHERE organization_id = ? AND job_id = ?",
                            (json.dumps(source, ensure_ascii=False), organization, job.job_id))
            else:
                source = stored
            context = self.db.fetchone(conn, "SELECT job_id, client_id FROM pro_conversion_contexts "
                                       "WHERE organization_id = ? AND job_id = ?",
                                       (organization, job.job_id))
            tax, name = normalized(source.get("holder_tax_id")), str(source.get("holder_name") or "").strip()
            client = None
            if not context and name and tax.isdigit() and len(tax) in (11, 14) and source.get("confidence") == "strong":
                client = self._client(conn, organization, owner, name[:160], tax)
            elif not context and not tax and source.get("confidence") != "ambiguous":
                matches = self._known_clients(conn, organization, source)
                if len(matches) == 1:
                    client = next(iter(matches))
                elif not matches and name and source.get("confidence") == "source_account" and account_identity(source):
                    client = self._client(conn, organization, owner, name[:160], None)
            if not context and client:
                account = self._link_accounts(conn, organization, owner, job.job_id, client, source)
                self.save_context_in_connection(conn, owner, job_id=job.job_id, client_id=client, account_id=account,
                                                period_ids=[], expected_version=0)
            linked_client = client or (context["client_id"] if context else None)
            if linked_client:
                self._prefer_document_name(conn, organization, linked_client, source, record)
            conn.commit()

    def _recover_native_source(self, owner, record):
        if (self.document_store is None or record.result is None
                or not str(record.job.document.filename).lower().endswith(".pdf")
                or record.expires_at <= datetime.now(timezone.utc)):
            return None
        original = (record.result.payload or {}).get("organization_source") or {}
        if original.get("version", 0) >= IDENTITY_VERSION:
            return None
        with self.db.connect() as conn:
            organization = self._organization(conn, owner)
            row = self.db.fetchone(conn, "SELECT source_json, expires_at FROM pro_received_statements "
                                   "WHERE organization_id = ? AND job_id = ?", (organization, record.job.job_id))
            context = self.db.fetchone(conn, "SELECT client_id FROM pro_conversion_contexts WHERE organization_id = ? AND job_id = ?",
                                       (organization, record.job.job_id))
            if context and context["client_id"]:
                return None
            if row and (_utc(row["expires_at"]) <= datetime.now(timezone.utc)
                        or json.loads(row["source_json"]).get("version", 0) >= IDENTITY_VERSION):
                return None
        try:
            from pypdf import PdfReader

            document = self.document_store.load(record.job.document)
            pages = [page.extract_text() or "" for page in PdfReader(BytesIO(document.raw_bytes)).pages]
            source = identify_native_statement({"bank_code": original.get("bank_code"), "bank_branch": original.get("branch"),
                "account_number": original.get("account_number"), "financial_document": {"metadata": {
                    "owner_name": original.get("holder_name"), "owner_tax_id": original.get("holder_tax_id")}}},
                raw_bytes=document.raw_bytes, page_texts=pages)
            return {**source, "native_identity_checked": True} if record.expires_at > datetime.now(timezone.utc) else None
        except Exception as exc:
            logger.warning("pro_receipt_identity_recovery_failed", extra={"job_id": record.job.job_id, "error_class": type(exc).__name__})
            return None

    def sync(self, owner, cursor=None):
        if self.repository is None:
            return None
        import base64

        from app.routers.pro_organization import decode_cursor

        summaries = self.repository.list_files_for_owner(identity_type="user", identity_id=owner, limit=100, before=decode_cursor(cursor))
        for summary in summaries:
            record = self.repository.get(summary.job_id)
            if record and record.job.identity.identity_type == "user" and record.job.identity.identity_id == owner:
                self.receive(record, recovered_source=self._recover_native_source(owner, record))
        if len(summaries) == 100:
            last = summaries[-1]
            return base64.urlsafe_b64encode(json.dumps([last.created_at.isoformat(), last.job_id]).encode()).decode().rstrip("=")
        return None

    def dashboard(self, owner, *, now=None, after="", limit=30, pending_after="", sort="id", query=""):
        now = _utc(now or datetime.now(timezone.utc)).isoformat()
        with self.db.connect() as conn:
            organization = self._organization(conn, owner)
            if not organization:
                return {"totals": {"statements": 0, "ready": 0, "attention": 0}, "clients": [], "pending": [],
                        "next_cursor": None, "pending_next_cursor": None, "client_count": 0, "pending_count": 0}
            totals = self.db.fetchone(conn, "SELECT COUNT(*) AS statements, "
                "COALESCE(SUM(CASE WHEN technical_status = 'ready' THEN 1 ELSE 0 END), 0) AS ready, "
                "COALESCE(SUM(CASE WHEN technical_status = 'attention' THEN 1 ELSE 0 END), 0) AS attention "
                "FROM pro_received_statements WHERE organization_id = ? AND expires_at > ?", (organization, now))
            client_filter, client_params = "c.id > ?", [after]
            # Authorize first, normalize/filter before pagination. Only hydrate one
            # page; Python normalization is identical on SQLite and PostgreSQL.
            identities = self.db.fetchall(conn,
                    "SELECT c.id, c.name, c.trade_name, c.tax_id FROM pro_clients c WHERE c.organization_id = ? AND EXISTS ("
                    "SELECT 1 FROM pro_conversion_contexts x JOIN pro_received_statements r "
                    "ON r.organization_id = x.organization_id AND r.job_id = x.job_id "
                    "WHERE x.organization_id = c.organization_id AND x.client_id = c.id)",
                    (organization,))
            identities = [row for row in identities if _matches_client(row, query)]
            client_count = len(identities)
            if sort == "name" or query.strip():
                identities = sorted(identities, key=_client_name_order if sort == "name" else lambda row: row["id"])
                start = next((index + 1 for index, row in enumerate(identities) if row["id"] == after), len(identities)) if after else 0
                client_params = [row["id"] for row in identities[start:start + limit + 1]]
                client_filter = f"c.id IN ({', '.join('?' for _ in client_params)})" if client_params else "1 = 0"
            rows = self.db.fetchall(conn, "SELECT c.id, c.name, c.tax_id, c.trade_name, c.updated_at, COUNT(*) AS historical_count, "
                "SUM(CASE WHEN r.expires_at > ? THEN 1 ELSE 0 END) AS available_count, "
                "SUM(CASE WHEN r.technical_status = 'attention' THEN 1 ELSE 0 END) AS attention_count, "
                "SUM(CASE WHEN r.technical_status = 'processing' THEN 1 ELSE 0 END) AS processing_count "
                "FROM pro_received_statements r JOIN pro_conversion_contexts x "
                "ON x.organization_id = r.organization_id AND x.job_id = r.job_id "
                "JOIN pro_clients c ON c.organization_id = x.organization_id AND c.id = x.client_id "
                f"WHERE r.organization_id = ? AND {client_filter} "
                "GROUP BY c.id, c.name, c.tax_id, c.trade_name, c.updated_at ORDER BY c.id LIMIT ?",
                (now, organization, *client_params, limit + 1))
            if sort == "name":
                rows = sorted(rows, key=_client_name_order)
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
            pending_count = self.db.fetchone(conn, "SELECT COUNT(*) AS total FROM pro_received_statements r "
                "LEFT JOIN pro_conversion_contexts x ON x.organization_id = r.organization_id AND x.job_id = r.job_id "
                "WHERE r.organization_id = ? AND x.client_id IS NULL AND r.expires_at > ?", (organization, now))["total"]
            pending = self.db.fetchall(conn, "SELECT r.*, COALESCE(x.version, 0) AS version FROM pro_received_statements r "
                "LEFT JOIN pro_conversion_contexts x ON x.organization_id = r.organization_id AND x.job_id = r.job_id "
                "WHERE r.organization_id = ? AND x.client_id IS NULL AND r.expires_at > ? AND r.job_id > ? ORDER BY r.job_id LIMIT 51",
                (organization, now, pending_after))
            return {"totals": dict(totals), "clients": clients, "client_count": client_count, "pending_count": pending_count,
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
            periods = [row["period_id"] for row in self.db.fetchall(conn,
                "SELECT period_id FROM pro_conversion_periods WHERE organization_id = ? AND job_id = ?", (organization, job_id))]
            self._write(conn, "DELETE FROM pro_received_statement_accounts WHERE organization_id = ? AND job_id = ?",
                        (organization, job_id))
            account_id = self._link_accounts(conn, organization, owner, job_id, client_id, source)
            result = self.save_context_in_connection(conn, owner, job_id=job_id, client_id=client_id, account_id=account_id,
                                                     period_ids=periods, expected_version=expected_version)
            self._confirm_identity(conn, organization, client_id)
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
            self._confirm_identity(conn, organization, client_id)
            conn.commit()
            return {"id": client_id}

    def _confirm_identity(self, conn, organization, client_id):
        self._write(conn, "INSERT INTO pro_client_identity_confirmations (organization_id, client_id, confirmed_at) "
                    "VALUES (?, ?, ?) ON CONFLICT (organization_id, client_id) DO NOTHING", (organization, client_id, self._now()))
