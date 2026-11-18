"""Owner-scoped shadow runs. Never invokes a live classification command."""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from app.application.classification_shadow.policy import FISCAL_VERSION, RULE_VERSION, classify_movement, extract_invoice_items
from app.application.errors import AnalysisEditConflictError, AnalysisNotFoundError


def digest(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


class ShadowService:
    def __init__(self, fiscal, transport=None):
        self.fiscal, self.repo, self.transport = fiscal, fiscal.repo, transport

    def _source(self, conn, client, kind, identifier):
        return (self.fiscal._movement if kind == "movement" else self.fiscal._document)(conn, client, identifier)

    def start(self, actor, client_id, *, source_kind, source_id, source_version):
        if source_kind not in {"movement", "invoice"}:
            raise ValueError("Origem não suportada.")
        fiscal_input = None
        if source_kind == "invoice":
            if self.transport is None:
                raise ValueError("O worker fiscal shadow ainda não está configurado.")
            raw, reference = self.fiscal.source(actor, client_id, source_id)
            if reference["file_type"] != "xml":
                raise ValueError("Envie o XML da NF-e para avaliar seus itens sem OCR.")
            fiscal_input = extract_invoice_items(raw)
        with self.repo._connect() as conn:
            client = self.fiscal._client(conn, actor, client_id)
            source = self._source(conn, client, source_kind, source_id)
            if source["version"] != source_version:
                raise AnalysisEditConflictError
            now = datetime.now(timezone.utc)
            payload = fiscal_input or {"items": [{"id": str(source["id"]), "amount": str(source["amount"]),
                                                  "description": source["description"], "provenance": source["provenance"]}]}
            engine = FISCAL_VERSION if fiscal_input else RULE_VERSION
            signature = digest({"kind": source_kind, "id": str(source_id), "version": source_version, "engine": engine, "data": payload})
            existing = conn.execute(f"""SELECT id FROM {self.repo.p}classification_shadow_runs
                WHERE organization_id=%s AND client_id=%s AND input_hash=%s AND expires_at>clock_timestamp()""",
                (client["organization_id"], client_id, signature)).fetchone()
            if existing:
                identifier = str(existing["id"])
            else:
                count = conn.execute(f"""SELECT count(*) AS n FROM {self.repo.p}classification_shadow_runs
                    WHERE organization_id=%s AND expires_at>clock_timestamp()""", (client["organization_id"],)).fetchone()["n"]
                if count >= 100:
                    raise ValueError("Limite de 100 avaliações ativas atingido.")
                identifier = str(uuid4())
                request = {"schema_version": 1, "run_id": identifier, "input_hash": signature, "engine_version": engine,
                           "expires_at": min(source["expires_at"], now + timedelta(hours=24)).isoformat(), **payload}
                result = None if fiscal_input else {"items": [classify_movement(payload["items"][0])]}
                conn.execute(f"""INSERT INTO {self.repo.p}classification_shadow_runs
                    (id,organization_id,client_id,actor_id,source_kind,source_id,source_version,engine_version,input_hash,
                     request,result,state,expires_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (identifier, client["organization_id"], client_id, actor, source_kind, source_id, source_version, engine, signature,
                     Jsonb(request), Jsonb(result) if result else None, "queued" if fiscal_input else "ready", request["expires_at"]))
        return self.read(actor, client_id, identifier)

    def _run(self, conn, actor, client_id, identifier):
        client = self.fiscal._client(conn, actor, client_id)
        run = conn.execute(f"""SELECT * FROM {self.repo.p}classification_shadow_runs WHERE id=%s
            AND organization_id=%s AND client_id=%s AND expires_at>clock_timestamp() FOR UPDATE""",
            (UUID(str(identifier)), client["organization_id"], client_id)).fetchone()
        if not run:
            raise AnalysisNotFoundError
        source = self._source(conn, client, run["source_kind"], run["source_id"])
        return run, source["version"] != run["source_version"]

    def read(self, actor, client_id, identifier):
        dispatch = None
        with self.repo._connect() as conn:
            run, stale = self._run(conn, actor, client_id, identifier)
            # Object reads are bounded by short transport timeouts. Source locks
            # prevent publication against a concurrently revised document.
            if run["state"] == "queued" and not stale and self.transport:
                result = self.transport.result(str(run["id"]))
                if result is not None:
                    expected = [str(item["id"]) for item in run["request"]["items"]]
                    if (result.get("input_hash") != run["input_hash"] or result.get("engine_version") != run["engine_version"]
                            or result.get("run_id") != str(run["id"]) or result.get("schema_version") != 1
                            or [str(item.get("id")) for item in result.get("items", [])] != expected):
                        raise ValueError("Resultado fiscal não corresponde à execução solicitada.")
                    run["result"], run["state"] = result, "ready"
                    conn.execute(f"UPDATE {self.repo.p}classification_shadow_runs SET result=%s,state='ready' WHERE id=%s",
                                 (Jsonb(result), run["id"]))
                elif datetime.now(timezone.utc) > run["created_at"] + timedelta(minutes=15):
                    run["state"] = "failed"
                    conn.execute(f"UPDATE {self.repo.p}classification_shadow_runs SET state='failed' WHERE id=%s", (run["id"],))
                elif run["next_dispatch_at"] <= datetime.now(timezone.utc) and run["dispatch_attempts"] < 5:
                    conn.execute(f"""UPDATE {self.repo.p}classification_shadow_runs SET
                        next_dispatch_at=clock_timestamp()+interval '2 minutes',dispatch_attempts=dispatch_attempts+1 WHERE id=%s""",
                        (run["id"],))
                    dispatch = run["request"]
            feedback = conn.execute(f"""SELECT item_id,verdict,comment FROM {self.repo.p}classification_shadow_feedback
                WHERE run_id=%s ORDER BY item_id""", (run["id"],)).fetchall()
            result = {"id": str(run["id"]), "state": run["state"], "stale": stale, "engine_version": run["engine_version"],
                      "source_version": run["source_version"], "items": (run["result"] or {}).get("items", []),
                      "feedback": feedback, "expires_at": run["expires_at"].isoformat(),
                      "metrics": {name: sum(row["verdict"] == name for row in feedback) for name in ("agree", "disagree", "unknown")}}
        if dispatch:
            try:
                self.transport.publish(dispatch)
            except (OSError, ConnectionError, RuntimeError):
                # The durable request remains queued. A later poll retries;
                # the SQS handler is idempotent by immutable request hash.
                pass
        return result

    def evaluate(self, actor, client_id, identifier, *, item_id, verdict, comment=""):
        if verdict not in {"agree", "disagree", "unknown"} or len(comment) > 500:
            raise ValueError("Avaliação inválida.")
        with self.repo._connect() as conn:
            run, stale = self._run(conn, actor, client_id, identifier)
            if stale or run["state"] != "ready":
                raise AnalysisEditConflictError
            if str(item_id) not in {str(item["id"]) for item in run["result"]["items"]}:
                raise ValueError("Item não pertence a esta avaliação.")
            conn.execute(f"""INSERT INTO {self.repo.p}classification_shadow_feedback(run_id,item_id,verdict,comment,actor_id)
                VALUES (%s,%s,%s,%s,%s) ON CONFLICT(run_id,item_id) DO UPDATE SET verdict=EXCLUDED.verdict,
                comment=EXCLUDED.comment,actor_id=EXCLUDED.actor_id,updated_at=clock_timestamp()""",
                (run["id"], str(item_id), verdict, comment, actor))
        return self.read(actor, client_id, identifier)
