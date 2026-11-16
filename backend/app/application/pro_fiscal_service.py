"""Owner-scoped PRO fiscal commands; no writes to bank transactions.

Commands serialize through a PRO fiscal advisory lock and lock source rows.
All displayed associations check their recorded versions.
"""

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from app.application.canonical_movements import MovementScope
from app.application.canonical_pro import CanonicalProService
from app.application.errors import AnalysisEditConflictError, AnalysisNotFoundError
from app.application.pro_financial_policy import (
    CATEGORIES,
    classify_tax_payment,
    possible_duplicate_note,
    revenue_summary,
    suggest_invoice_matches,
)
from app.application.pro_fiscal_documents import fiscal_date, fiscal_money, tax_identifier


class ProFiscalService:
    def __init__(self, repository, *, objects=None, queue=None, ttl_seconds=86400):
        self.repo, self.objects, self.queue = repository, objects, queue
        self.ttl = max(60, min(ttl_seconds, 86400))

    def _client(self, conn, actor, client_id, *, lock=True):
        CanonicalProService(self.repo, None).assert_entitled(conn, actor)
        client = conn.execute(
            f"""SELECT c.*,o.owner_user_id FROM {self.repo.p}pro_clients c
            JOIN {self.repo.p}pro_organizations o ON o.id=c.organization_id
            JOIN {self.repo.p}users u ON u.id=o.owner_user_id AND u.is_active
            WHERE c.id=%s AND o.owner_user_id=%s""",
            (client_id, actor),
        ).fetchone()
        if not client:
            raise AnalysisNotFoundError
        if lock:
            # A PRO-only advisory lock serializes allocations without reversing
            # the canonical batch/job/organization row-lock order.
            conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (self.repo.schema + ":pro-fiscal:" + client["organization_id"],)
            )
        return client

    def _document(self, conn, client, document_id):
        row = conn.execute(
            f"""SELECT * FROM {self.repo.p}pro_fiscal_documents
            WHERE id=%s AND organization_id=%s AND client_id=%s AND expires_at>clock_timestamp() FOR UPDATE""",
            (UUID(str(document_id)), client["organization_id"], client["id"]),
        ).fetchone()
        if not row:
            raise AnalysisNotFoundError
        return row

    def _movement(self, conn, client, movement_id, *, allow_deleted=False):
        row = conn.execute(
            f"""SELECT m.*,a.job_id FROM {self.repo.p}bank_movements m
            JOIN {self.repo.p}canonical_analyses a ON a.analysis_id=m.analysis_id
            WHERE m.id=%s AND m.organization_id=%s AND m.client_id=%s {"" if allow_deleted else "AND NOT m.is_deleted"}""",
            (UUID(str(movement_id)), client["organization_id"], client["id"]),
        ).fetchone()
        if not row:
            raise AnalysisNotFoundError
        scope = MovementScope(client["owner_user_id"], client["organization_id"], row["job_id"], row["analysis_id"], client["id"])
        analysis, source = self.repo._analysis(conn, scope)
        if not analysis["read_enabled"] or not analysis["ui_enabled"]:
            raise AnalysisNotFoundError
        # Reread after canonical source locks, so a concurrent edit cannot leave
        # a command recorded against a stale value/version.
        row = conn.execute(
            f"""SELECT * FROM {self.repo.p}bank_movements WHERE id=%s
            {"" if allow_deleted else "AND NOT is_deleted"} FOR UPDATE""",
            (UUID(str(movement_id)),),
        ).fetchone()
        if not row:
            raise AnalysisNotFoundError
        row["expires_at"] = min(analysis["expires_at"], source["expires_at"], source["batch_expires_at"])
        self.repo._unexpired(conn, scope)
        return row

    @staticmethod
    def _direction(payload, client):
        tax = tax_identifier(client["tax_id"])
        issuer, recipient = payload["issuer"]["tax_id"], payload["recipient"]["tax_id"]
        if tax and tax == issuer and tax != recipient:
            return "issued"
        if tax and tax == recipient and tax != issuer:
            return "received"
        return "unconfirmed"

    def _note(self, row, client):
        return {
            **row["payload"],
            "id": str(row["id"]),
            "version": row["version"],
            "direction": self._direction(row["payload"], client),
            "expires_at": row["expires_at"].isoformat(),
        }

    def prepare_import(self, actor, client_id, *, filename, size_bytes, digest):
        with self.repo._connect() as conn:
            client = self._client(conn, actor, client_id)
            existing = conn.execute(
                f"""SELECT * FROM {self.repo.p}pro_fiscal_imports
                WHERE organization_id=%s AND client_id=%s AND digest=%s""",
                (client["organization_id"], client_id, digest),
            ).fetchone()
            if existing:
                if existing["expires_at"] <= datetime.now(timezone.utc):
                    raise ValueError("A importação anterior expirou. Aguarde a limpeza dos arquivos antes de reenviar.")
                upload = self.objects.prepare(existing["reference"]) if existing["state"] == "uploading" else None
                return {"id": str(existing["id"]), "state": existing["state"], "upload": upload}
            # Bound retained storage/OCR cost independently of the Free quota.
            count = conn.execute(
                f"""SELECT count(*) AS n FROM {self.repo.p}pro_fiscal_imports
                WHERE organization_id=%s AND expires_at>clock_timestamp()""",
                (client["organization_id"],),
            ).fetchone()["n"]
            if count >= 100:
                raise ValueError("Limite de 100 documentos fiscais ativos atingido.")
            reference = self.objects.reference(filename, size_bytes, digest)
            upload = self.objects.prepare(reference)
            identifier = uuid4()
            conn.execute(
                f"""INSERT INTO {self.repo.p}pro_fiscal_imports
                (id,organization_id,client_id,actor_id,reference,digest,state,expires_at)
                VALUES (%s,%s,%s,%s,%s,%s,'uploading',clock_timestamp()+%s)""",
                (identifier, client["organization_id"], client_id, actor, Jsonb(reference), digest, timedelta(seconds=self.ttl)),
            )
            return {"id": str(identifier), "state": "uploading", "upload": upload}

    def submit_import(self, actor, client_id, import_id):
        with self.repo._connect() as conn:
            client = self._client(conn, actor, client_id)
            row = conn.execute(
                f"""SELECT * FROM {self.repo.p}pro_fiscal_imports WHERE id=%s AND organization_id=%s
                AND client_id=%s AND expires_at>clock_timestamp() FOR UPDATE""",
                (UUID(str(import_id)), client["organization_id"], client_id),
            ).fetchone()
            if not row:
                raise AnalysisNotFoundError
            if row["state"] == "uploading":
                self.objects.verify(row["reference"])
                conn.execute(f"UPDATE {self.repo.p}pro_fiscal_imports SET state='queued' WHERE id=%s", (row["id"],))
        # Durable outbox is the queued row itself. Failure to publish is retried by
        # maintenance; never tell the user that a completed upload was discarded.
        try:
            self.dispatch()
        except (OSError, ConnectionError, RuntimeError):
            pass
        return {"id": str(import_id), "state": "queued" if row["state"] == "uploading" else row["state"]}

    def dispatch(self):
        sent = 0
        with self.repo._connect() as conn:
            rows = conn.execute(f"""SELECT id FROM {self.repo.p}pro_fiscal_imports
                WHERE (state='queued' OR (state='processing' AND lease_until<clock_timestamp()))
                    AND expires_at>clock_timestamp() AND next_dispatch_at<=clock_timestamp()
                ORDER BY created_at LIMIT 50 FOR UPDATE SKIP LOCKED""").fetchall()
            for row in rows:
                self.queue.publish(str(row["id"]))
                conn.execute(
                    f"""UPDATE {self.repo.p}pro_fiscal_imports
                    SET next_dispatch_at=clock_timestamp()+interval '5 minutes' WHERE id=%s""",
                    (row["id"],),
                )
                sent += 1
        return sent

    def complete_import(self, import_id, parse):
        # Short claim transaction, then object IO/OCR without holding account locks.
        with self.repo._connect() as conn:
            row = conn.execute(
                f"""SELECT * FROM {self.repo.p}pro_fiscal_imports WHERE id=%s
                AND expires_at>clock_timestamp() FOR UPDATE""",
                (UUID(str(import_id)),),
            ).fetchone()
            if not row or row["state"] in {"ready", "failed", "uploading"}:
                return
            if row["state"] == "processing" and row["lease_until"] > datetime.now(timezone.utc):
                raise ConnectionError("Fiscal import already processing")
            conn.execute(
                f"""UPDATE {self.repo.p}pro_fiscal_imports SET state='processing',attempts=attempts+1,
                lease_until=clock_timestamp()+interval '12 minutes' WHERE id=%s""",
                (row["id"],),
            )
            attempt = row["attempts"] + 1
        try:
            with self.repo._connect() as conn:
                self._client(conn, row["actor_id"], row["client_id"])
            payload = parse(self.objects.read(row["reference"]), row["reference"]["file_type"])
            with self.repo._connect() as conn:
                client = self._client(conn, row["actor_id"], row["client_id"])
                current = conn.execute(
                    f"""SELECT * FROM {self.repo.p}pro_fiscal_imports WHERE id=%s
                    AND state='processing' AND attempts=%s AND expires_at>clock_timestamp() FOR UPDATE""",
                    (row["id"], attempt),
                ).fetchone()
                if not current:
                    raise AnalysisNotFoundError
                if self._direction(payload, client) == "unconfirmed":
                    payload["review_required"] = True
                existing = conn.execute(
                    f"""SELECT * FROM {self.repo.p}pro_fiscal_documents
                    WHERE organization_id=%s AND client_id=%s AND identity_key=%s FOR UPDATE""",
                    (client["organization_id"], client["id"], payload["identity_key"]),
                ).fetchone()
                if existing:
                    keys = ("issuer", "recipient", "gross_amount", "net_amount", "issued_on", "status")
                    if any(existing["payload"].get(k) != payload.get(k) for k in keys):
                        raise ValueError("A mesma nota já existe com dados diferentes. Revise a nota existente e a origem enviada.")
                    if existing["expires_at"] <= datetime.now(timezone.utc):
                        raise ValueError("A nota original expirou; a reimportação não renova sua retenção.")
                    document_id = existing["id"]
                else:
                    document_id = uuid4()
                    conn.execute(
                        f"""INSERT INTO {self.repo.p}pro_fiscal_documents
                        (id,organization_id,client_id,identity_key,payload,expires_at) VALUES (%s,%s,%s,%s,%s,%s)""",
                        (document_id, client["organization_id"], client["id"], payload["identity_key"], Jsonb(payload), row["expires_at"]),
                    )
                replaced = payload.get("replaced_document_key")
                if replaced:
                    prior = conn.execute(
                        f"""SELECT * FROM {self.repo.p}pro_fiscal_documents
                        WHERE organization_id=%s AND client_id=%s AND identity_key=%s
                        AND id<>%s AND expires_at>clock_timestamp() FOR UPDATE""",
                        (client["organization_id"], client["id"], "nfse:NFS" + replaced.removeprefix("NFS"), document_id),
                    ).fetchone()
                    if prior:
                        changed = {**prior["payload"], "status": "substituted"}
                        conn.execute(
                            f"UPDATE {self.repo.p}pro_fiscal_documents SET payload=%s,version=version+1 WHERE id=%s",
                            (Jsonb(changed), prior["id"]),
                        )
                        audit = {
                            "kind": "document_replacement",
                            "id": str(prior["id"]),
                            "source_import": str(row["id"]),
                            "before": prior["payload"],
                            "after": changed,
                        }
                        conn.execute(
                            f"""INSERT INTO {self.repo.p}pro_fiscal_operations
                            (organization_id,client_id,operation_id,request_hash,actor_id,result,expires_at)
                            VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                            (
                                client["organization_id"],
                                client["id"],
                                "replacement:" + str(row["id"]),
                                hashlib.sha256(json.dumps(audit, sort_keys=True).encode()).hexdigest(),
                                row["actor_id"],
                                Jsonb(audit),
                                min(row["expires_at"], prior["expires_at"]),
                            ),
                        )
                conn.execute(
                    f"""UPDATE {self.repo.p}pro_fiscal_imports SET state='ready',document_id=%s,
                    extraction=%s,lease_until=NULL,expires_at=LEAST(expires_at,%s) WHERE id=%s""",
                    (document_id, Jsonb(payload), existing["expires_at"] if existing else row["expires_at"], row["id"]),
                )
        except Exception as exc:
            final = isinstance(exc, (ValueError, AnalysisNotFoundError)) or attempt >= 3
            with self.repo._connect() as conn:
                conn.execute(
                    f"""UPDATE {self.repo.p}pro_fiscal_imports SET state=%s,error=%s,lease_until=NULL,
                    next_dispatch_at=clock_timestamp() WHERE id=%s AND attempts=%s AND state='processing' """,
                    (
                        "failed" if final else "queued",
                        str(exc)
                        if isinstance(exc, ValueError)
                        else "Não foi possível processar a nota. Confira o acesso e tente novamente.",
                        row["id"],
                        attempt,
                    ),
                )
            if not final:
                raise

    def _command(self, conn, client, actor, operation_id, request, execute):
        if not operation_id or len(operation_id) > 100:
            raise ValueError("Identificador de operação inválido.")
        digest = hashlib.sha256(json.dumps(request, sort_keys=True, default=str).encode()).hexdigest()
        previous = conn.execute(
            f"""SELECT * FROM {self.repo.p}pro_fiscal_operations
            WHERE organization_id=%s AND operation_id=%s AND expires_at>clock_timestamp()""",
            (client["organization_id"], operation_id),
        ).fetchone()
        if previous:
            if previous["request_hash"] != digest or previous["client_id"] != client["id"]:
                raise AnalysisEditConflictError
            return previous["result"]
        result, expiry = execute()
        current_client = self._client(conn, actor, client["id"])
        if current_client["tax_id"] != client["tax_id"]:
            raise AnalysisEditConflictError
        if expiry <= datetime.now(timezone.utc):
            raise AnalysisNotFoundError
        conn.execute(
            f"""INSERT INTO {self.repo.p}pro_fiscal_operations
            (organization_id,client_id,operation_id,request_hash,actor_id,result,expires_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (client["organization_id"], client["id"], operation_id, digest, actor, Jsonb(result), expiry),
        )
        return result

    def classify(self, actor, client_id, movement_id, *, category, expected_version, movement_version, operation_id, details=None):
        if category not in CATEGORIES:
            raise ValueError("Classificação inválida.")
        details = details or {}
        if set(details) - {"competence", "revenue_code", "note"} or any(len(str(v)) > 300 for v in details.values()):
            raise ValueError("Detalhes inválidos.")
        if details.get("competence"):
            fiscal_date(details["competence"] + "-01")
        with self.repo._connect() as conn:
            client = self._client(conn, actor, client_id)
            row = self._movement(conn, client, movement_id)

            def execute():
                prior = conn.execute(
                    f"SELECT * FROM {self.repo.p}pro_movement_classifications WHERE movement_id=%s", (row["id"],)
                ).fetchone()
                if row["version"] != movement_version or (prior["version"] if prior else 0) != expected_version:
                    raise AnalysisEditConflictError
                if (category == "operating_receipt" and row["amount"] <= 0) or (
                    (category.startswith("tax_") or category == "operating_payment") and row["amount"] >= 0
                ):
                    raise ValueError("A categoria não corresponde ao sentido do movimento.")
                version = expected_version + 1
                conn.execute(
                    f"""INSERT INTO {self.repo.p}pro_movement_classifications
                    (movement_id,organization_id,client_id,category,details,movement_version,version,actor_id)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(movement_id) DO UPDATE SET
                    category=excluded.category,details=excluded.details,movement_version=excluded.movement_version,
                    version=excluded.version,actor_id=excluded.actor_id,updated_at=clock_timestamp()""",
                    (row["id"], client["organization_id"], client_id, category, Jsonb(details), row["version"], version, actor),
                )
                return {
                    "movement_id": str(row["id"]),
                    "category": category,
                    "details": details,
                    "version": version,
                    "before": json.loads(json.dumps(dict(prior), default=str)) if prior else None,
                }, row["expires_at"]

            # Audit JSON keeps previous values with explicit serialization.
            return self._command(
                conn,
                client,
                actor,
                operation_id,
                {
                    "kind": "classify",
                    "movement": str(movement_id),
                    "category": category,
                    "details": details,
                    "version": expected_version,
                    "movement_version": movement_version,
                },
                execute,
            )

    def review_document(self, actor, client_id, document_id, *, expected_version, operation_id, changes):
        allowed = {
            "number",
            "series",
            "issued_on",
            "competence_on",
            "gross_amount",
            "net_amount",
            "operation",
            "status",
            "issuer_tax_id",
            "recipient_tax_id",
        }
        if set(changes) - allowed:
            raise ValueError("Campos de revisão inválidos.")
        with self.repo._connect() as conn:
            client = self._client(conn, actor, client_id)
            row = self._document(conn, client, document_id)

            def execute():
                if row["version"] != expected_version:
                    raise AnalysisEditConflictError
                payload = {**row["payload"], **changes}
                for party in ("issuer", "recipient"):
                    key = party + "_tax_id"
                    if key in changes:
                        value = tax_identifier(payload.pop(key))
                        if not value:
                            raise ValueError("Confira o CPF/CNPJ informado na nota.")
                        payload[party] = {**payload[party], "tax_id": value}
                for key in ("gross_amount", "net_amount"):
                    if payload.get(key) is not None:
                        payload[key] = fiscal_money(payload[key])
                for key in ("issued_on", "competence_on"):
                    if payload.get(key):
                        payload[key] = fiscal_date(payload[key])
                if (
                    not payload.get("issued_on")
                    or not payload.get("gross_amount")
                    or not payload.get("number")
                    or len(str(payload["number"])) > 60
                    or len(str(payload.get("series") or "")) > 20
                    or self._direction(payload, client) == "unconfirmed"
                ):
                    raise ValueError("Confira data, valor, número e CPF/CNPJ da empresa antes de confirmar.")
                if payload["status"] not in {"reported", "cancelled", "substituted", "duplicate"} or payload["operation"] not in {
                    "sale",
                    "return",
                    "other",
                    "unknown",
                }:
                    raise ValueError("Situação ou natureza inválida.")
                payload["review_required"] = False
                conn.execute(
                    f"""UPDATE {self.repo.p}pro_fiscal_documents SET payload=%s,version=version+1 WHERE id=%s""",
                    (Jsonb(payload), row["id"]),
                )
                return {"id": str(row["id"]), "version": expected_version + 1, "before": row["payload"], "after": payload}, row[
                    "expires_at"
                ]

            return self._command(
                conn,
                client,
                actor,
                operation_id,
                {"kind": "review", "id": str(document_id), "version": expected_version, "changes": changes},
                execute,
            )

    def link(self, actor, client_id, document_id, movement_id, *, amount, document_version, movement_version, operation_id):
        amount = Decimal(fiscal_money(amount))
        with self.repo._connect() as conn:
            client = self._client(conn, actor, client_id)
            movement = self._movement(conn, client, movement_id)
            doc = self._document(conn, client, document_id)

            def execute():
                note = self._note(doc, client)
                if doc["version"] != document_version or movement["version"] != movement_version:
                    raise AnalysisEditConflictError
                if (
                    note["review_required"]
                    or note["status"] not in {"reported", "confirmed"}
                    or note["direction"] == "unconfirmed"
                    or movement["currency"] != note["currency"]
                    or (movement["amount"] > 0) != (note["direction"] == "issued")
                    or amount <= 0
                ):
                    raise ValueError("A nota e o movimento precisam de revisão antes do vínculo.")
                peers = conn.execute(
                    f"""SELECT payload FROM {self.repo.p}pro_fiscal_documents d
                    WHERE organization_id=%s AND client_id=%s AND id<>%s AND expires_at>clock_timestamp()
                    AND payload->>'status' IN ('reported','confirmed') LIMIT 101""",
                    (client["organization_id"], client_id, doc["id"]),
                ).fetchall()
                if any(possible_duplicate_note(note, other["payload"]) for other in peers):
                    raise ValueError("Há uma possível duplicata documental. Confira as notas antes de vincular.")
                # Stale allocations still reserve their amounts until explicitly
                # undone. Editing a source never silently releases a payment.
                links = conn.execute(
                    f"""SELECT * FROM {self.repo.p}pro_fiscal_links WHERE state='confirmed'
                    AND (document_id=%s OR movement_id=%s) FOR UPDATE""",
                    (doc["id"], movement["id"]),
                ).fetchall()
                if any(link["document_id"] == doc["id"] and link["movement_id"] == movement["id"] for link in links):
                    raise AnalysisEditConflictError
                note_used = sum((r["amount"] for r in links if r["document_id"] == doc["id"]), Decimal(0))
                movement_used = sum((r["amount"] for r in links if r["movement_id"] == movement["id"]), Decimal(0))
                if amount + note_used > Decimal(note["net_amount"] or note["gross_amount"]) or amount + movement_used > abs(
                    movement["amount"]
                ):
                    raise ValueError("O valor excede o saldo disponível da nota ou do movimento.")
                identifier = uuid4()
                conn.execute(
                    f"""INSERT INTO {self.repo.p}pro_fiscal_links
                    (id,organization_id,client_id,document_id,movement_id,amount,document_version,movement_version,state,actor_id)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'confirmed',%s)""",
                    (
                        identifier,
                        client["organization_id"],
                        client_id,
                        doc["id"],
                        movement["id"],
                        amount,
                        document_version,
                        movement_version,
                        actor,
                    ),
                )
                return {
                    "id": str(identifier),
                    "document_id": str(doc["id"]),
                    "movement_id": str(movement["id"]),
                    "amount": str(amount),
                    "version": 1,
                }, min(doc["expires_at"], movement["expires_at"])

            return self._command(
                conn,
                client,
                actor,
                operation_id,
                {
                    "kind": "link",
                    "doc": str(document_id),
                    "movement": str(movement_id),
                    "amount": str(amount),
                    "dv": document_version,
                    "mv": movement_version,
                },
                execute,
            )

    def unlink(self, actor, client_id, link_id, *, expected_version, operation_id):
        with self.repo._connect() as conn:
            client = self._client(conn, actor, client_id)
            link = conn.execute(
                f"""SELECT * FROM {self.repo.p}pro_fiscal_links WHERE id=%s AND organization_id=%s
                AND client_id=%s FOR UPDATE""",
                (UUID(str(link_id)), client["organization_id"], client_id),
            ).fetchone()
            if not link:
                raise AnalysisNotFoundError
            doc = self._document(conn, client, link["document_id"])
            movement = self._movement(conn, client, link["movement_id"], allow_deleted=True)

            def execute():
                if link["version"] != expected_version or link["state"] != "confirmed":
                    raise AnalysisEditConflictError
                conn.execute(f"UPDATE {self.repo.p}pro_fiscal_links SET state='undone',version=version+1 WHERE id=%s", (link["id"],))
                return {"id": str(link["id"]), "version": expected_version + 1, "state": "undone"}, min(
                    doc["expires_at"], movement["expires_at"]
                )

            return self._command(conn, client, actor, operation_id, {"kind": "unlink", "id": str(link_id), "v": expected_version}, execute)

    def source(self, actor, client_id, document_id):
        with self.repo._connect() as conn:
            client = self._client(conn, actor, client_id)
            self._document(conn, client, document_id)
            row = conn.execute(
                f"""SELECT reference FROM {self.repo.p}pro_fiscal_imports
                WHERE document_id=%s AND expires_at>clock_timestamp() ORDER BY created_at LIMIT 1""",
                (UUID(str(document_id)),),
            ).fetchone()
            if not row:
                raise AnalysisNotFoundError
            content = self.objects.read(row["reference"])
            self._client(conn, actor, client_id)
            self._document(conn, client, document_id)  # IO cannot extend access past expiry.
            return content, row["reference"]

    def workspace(self, actor, client_id, *, month, account=None, offset=0, document_id=None, movement_id=None, all_movements=False):
        start = date.fromisoformat(month + "-01")
        end = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        if not 2000 <= start.year <= 2100 or not 0 <= offset <= 10000:
            raise ValueError("Período ou paginação inválidos.")
        with self.repo._connect() as conn:
            conn.execute("SET LOCAL statement_timeout='10s'")
            client = self._client(conn, actor, client_id)
            if account and account != "unidentified":
                if not conn.execute(
                    f"""SELECT 1 FROM {self.repo.p}pro_accounts WHERE id=%s
                    AND organization_id=%s AND client_id=%s""",
                    (account, client["organization_id"], client_id),
                ).fetchone():
                    raise AnalysisNotFoundError
            # Bounded server projection; only one page of movements leaves API.
            # Every bank row uses the same source/entitlement/TTL guards as Central.
            rows = conn.execute(
                f"""SELECT m.*,a.job_id,a.version AS analysis_version,j.batch_id,
                    j.document->>'filename' AS filename,
                    LEAST(a.expires_at,j.expires_at,b.expires_at) AS expires_at,
                    c.category,c.details,c.version AS classification_version,c.movement_version AS classified_version,
                    count(*) OVER (PARTITION BY m.account_id,m.movement_date) AS day_count,
                    (SELECT count(DISTINCT other.analysis_id)>1 FROM {self.repo.p}bank_movements other
                     JOIN {self.repo.p}canonical_analyses oa ON oa.analysis_id=other.analysis_id
                     WHERE other.organization_id=m.organization_id AND other.client_id=m.client_id
                     AND other.account_id IS NOT DISTINCT FROM m.account_id AND other.movement_date=m.movement_date
                     AND NOT other.is_deleted AND oa.expires_at>clock_timestamp()) AS possible_duplicate
                FROM {self.repo.p}bank_movements m
                JOIN {self.repo.p}canonical_analyses a ON a.analysis_id=m.analysis_id
                JOIN {self.repo.p}conversion_jobs j ON j.job_id=a.job_id AND j.result_analysis_id=a.analysis_id
                JOIN {self.repo.p}conversion_batches b ON b.batch_id=j.batch_id
                JOIN {self.repo.p}pro_conversion_contexts x ON x.job_id=a.job_id AND x.organization_id=a.organization_id
                LEFT JOIN {self.repo.p}pro_movement_classifications c ON c.movement_id=m.id
                WHERE m.organization_id=%s AND m.client_id=%s AND x.client_id=m.client_id
                    AND a.client_id=m.client_id AND a.read_enabled AND a.ui_enabled AND NOT m.is_deleted
                    AND j.status='completed' AND b.identity_type='user' AND b.identity_id=%s
                    AND j.identity->>'identity_type'='user' AND j.identity->>'identity_id'=%s
                    AND LEAST(a.expires_at,j.expires_at,b.expires_at)>clock_timestamp()
                    AND m.movement_date>=%s AND m.movement_date<%s
                    AND (%s::text IS NULL OR COALESCE(m.account_id,'unidentified')=%s)
                ORDER BY m.movement_date,m.id LIMIT 10001""",
                (client["organization_id"], client_id, actor, actor, start, end, account, account),
            ).fetchall()
            truncated = len(rows) > 10000
            rows = rows[:10000]
            documents = conn.execute(
                f"""SELECT * FROM {self.repo.p}pro_fiscal_documents WHERE organization_id=%s
                AND client_id=%s AND expires_at>clock_timestamp() ORDER BY created_at DESC LIMIT 101""",
                (client["organization_id"], client_id),
            ).fetchall()
            notes = [self._note(row, client) for row in documents]
            for note in notes:
                if note["direction"] == "unconfirmed":
                    note["review_required"] = True
                peers = [other for other in notes if other["status"] in {"reported", "confirmed"} and possible_duplicate_note(note, other)]
                if len(peers) > 1:
                    note["review_required"] = True
                    note["possible_duplicate"] = True
            current_notes = [n for n in notes if n.get("issued_on") and start.isoformat() <= n["issued_on"] < end.isoformat()]
            links = conn.execute(
                f"""SELECT l.*,d.version AS current_document_version,m.version AS current_movement_version,
                    m.is_deleted,LEAST(d.expires_at,a.expires_at,j.expires_at,b.expires_at) AS expires_at,
                    a.read_enabled,a.ui_enabled,x.client_id AS source_client_id,m.amount AS movement_amount,
                    m.description AS movement_description,m.movement_date,m.currency AS movement_currency
                FROM {self.repo.p}pro_fiscal_links l JOIN {self.repo.p}pro_fiscal_documents d ON d.id=l.document_id
                JOIN {self.repo.p}bank_movements m ON m.id=l.movement_id
                JOIN {self.repo.p}canonical_analyses a ON a.analysis_id=m.analysis_id
                JOIN {self.repo.p}conversion_jobs j ON j.job_id=a.job_id AND j.result_analysis_id=a.analysis_id
                JOIN {self.repo.p}conversion_batches b ON b.batch_id=j.batch_id
                JOIN {self.repo.p}pro_conversion_contexts x ON x.job_id=a.job_id AND x.organization_id=a.organization_id
                WHERE l.organization_id=%s AND l.client_id=%s AND l.state='confirmed'
                    AND j.status='completed' AND b.identity_type='user' AND b.identity_id=%s
                    AND j.identity->>'identity_type'='user' AND j.identity->>'identity_id'=%s
                    AND LEAST(d.expires_at,a.expires_at,j.expires_at,b.expires_at)>clock_timestamp()
                ORDER BY l.created_at LIMIT 10001""",
                (client["organization_id"], client_id, actor, actor),
            ).fetchall()
            note_by_id = {note["id"]: note for note in notes}
            visible_links = [
                {
                    "id": str(r["id"]),
                    "document_id": str(r["document_id"]),
                    "movement_id": str(r["movement_id"]),
                    "amount": str(r["amount"]),
                    "version": r["version"],
                    "movement_description": r["movement_description"],
                    "movement_date": r["movement_date"].isoformat(),
                    "currency": r["movement_currency"],
                    "state": "confirmed"
                    if r["document_version"] == r["current_document_version"]
                    and r["movement_version"] == r["current_movement_version"]
                    and not r["is_deleted"]
                    and str(r["document_id"]) in note_by_id
                    and not note_by_id[str(r["document_id"])]["review_required"]
                    and note_by_id[str(r["document_id"])]["status"] in {"reported", "confirmed"}
                    and (r["movement_amount"] > 0) == (note_by_id[str(r["document_id"])]["direction"] == "issued")
                    else "review_required",
                }
                for r in links
                if r["read_enabled"] and r["ui_enabled"] and r["source_client_id"] == client_id
            ]
            movements = []
            for r in rows:
                classification = None
                if r["category"]:
                    classification = {
                        "category": r["category"],
                        "details": r["details"],
                        "version": r["classification_version"],
                        "state": "confirmed" if r["classified_version"] == r["version"] else "review_required",
                    }
                movements.append(
                    {
                        "id": str(r["id"]),
                        "date": r["movement_date"].isoformat(),
                        "amount": str(r["amount"]),
                        "currency": r["currency"],
                        "description": r["description"],
                        "version": r["version"],
                        "account_id": r["account_id"],
                        "possible_duplicate": r["possible_duplicate"],
                        "classification": classification,
                        "tax_suggestion": classify_tax_payment(str(r["amount"]), r["description"]),
                        "source": {"job_id": r["job_id"], "batch_id": r["batch_id"], "filename": r["filename"]},
                    }
                )
            summary = revenue_summary(current_notes, movements)
            summary["pending_documents"] += sum(not n.get("issued_on") for n in notes)
            truncated = truncated or len(links) > 10000
            if truncated:
                summary.update(
                    operating_receipts=None,
                    unclassified_inflows=None,
                    excluded_inflows=None,
                    tax_payments={},
                    consolidation="review_required",
                )
            summary["account_filter_applies_to"] = "bank_movements_only"
            imports = conn.execute(
                f"""SELECT id,state,error,reference->>'filename' AS filename,document_id,expires_at
                FROM {self.repo.p}pro_fiscal_imports WHERE organization_id=%s AND client_id=%s
                AND expires_at>clock_timestamp() ORDER BY created_at DESC LIMIT 100""",
                (client["organization_id"], client_id),
            ).fetchall()
            # Recheck source availability after all projection queries. A changed
            # movement or revoked source cannot be returned as a fresh snapshot.
            if rows:
                checks = conn.execute(
                    f"""SELECT m.id,m.version,a.version AS analysis_version FROM {self.repo.p}bank_movements m
                    JOIN {self.repo.p}canonical_analyses a ON a.analysis_id=m.analysis_id
                    JOIN {self.repo.p}conversion_jobs j ON j.job_id=a.job_id
                    JOIN {self.repo.p}conversion_batches b ON b.batch_id=j.batch_id
                    JOIN {self.repo.p}pro_conversion_contexts x ON x.job_id=a.job_id AND x.organization_id=a.organization_id
                    WHERE m.id=ANY(%s) AND NOT m.is_deleted AND a.read_enabled AND a.ui_enabled
                    AND m.client_id=%s AND a.client_id=m.client_id AND x.client_id=m.client_id
                    AND m.organization_id=%s AND j.result_analysis_id=a.analysis_id AND j.status='completed'
                    AND b.identity_type='user' AND b.identity_id=%s
                    AND j.identity->>'identity_type'='user' AND j.identity->>'identity_id'=%s
                    AND LEAST(a.expires_at,j.expires_at,b.expires_at)>clock_timestamp()""",
                    ([r["id"] for r in rows], client_id, client["organization_id"], actor, actor),
                ).fetchall()
                if {(str(r["id"]), r["version"], r["analysis_version"]) for r in checks} != {
                    (str(r["id"]), r["version"], r["analysis_version"]) for r in rows
                }:
                    raise AnalysisEditConflictError
            final_client = self._client(conn, actor, client_id)
            if final_client["tax_id"] != client["tax_id"]:
                raise AnalysisEditConflictError
            expiries = [r["expires_at"] for r in rows + documents + imports + links]
            expiry = min(expiries) if expiries else None
            if expiry and expiry <= datetime.now(timezone.utc):
                raise AnalysisEditConflictError
            selected = next((n for n in notes if n["id"] == document_id), None)
            if movement_id:
                focused = next((i for i, movement in enumerate(movements) if movement["id"] == movement_id), None)
                if focused is None:
                    raise AnalysisNotFoundError
                offset = (focused // 50) * 50
            return {
                "client": {"id": client_id, "name": client["name"], "tax_id": client["tax_id"]},
                "month": month,
                "documents": notes,
                "imports": [
                    {
                        **r,
                        "id": str(r["id"]),
                        "document_id": str(r["document_id"]) if r["document_id"] else None,
                        "expires_at": r["expires_at"].isoformat(),
                    }
                    for r in imports
                ],
                "movements": movements if all_movements else movements[offset : offset + 50],
                "movement_count": len(movements),
                "next_offset": offset + 50 if offset + 50 < len(movements) else None,
                "links": visible_links,
                "summary": summary,
                "truncated": truncated,
                "suggestions": suggest_invoice_matches(selected, movements) if selected else [],
                "categories": CATEGORIES,
                "access_expires_at": expiry.isoformat() if expiry else None,
            }

    def cleanup(self):
        removed = 0
        with self.repo._connect() as conn:
            if not conn.execute("SELECT to_regclass(%s) AS relation", (self.repo.p + "pro_fiscal_imports",)).fetchone()["relation"]:
                return 0  # Empty-schema rollback has no retained fiscal data.
            rows = conn.execute(f"""SELECT * FROM {self.repo.p}pro_fiscal_imports WHERE expires_at<=clock_timestamp()
                ORDER BY expires_at LIMIT 100 FOR UPDATE SKIP LOCKED""").fetchall()
            for row in rows:
                self.objects.delete(row["reference"])
                conn.execute(f"DELETE FROM {self.repo.p}pro_fiscal_imports WHERE id=%s", (row["id"],))
                removed += 1
            conn.execute(f"""DELETE FROM {self.repo.p}pro_fiscal_documents d WHERE expires_at<=clock_timestamp()
                AND NOT EXISTS(SELECT 1 FROM {self.repo.p}pro_fiscal_imports i WHERE i.document_id=d.id)""")
            conn.execute(f"DELETE FROM {self.repo.p}pro_fiscal_operations WHERE expires_at<=clock_timestamp()")
        return removed
