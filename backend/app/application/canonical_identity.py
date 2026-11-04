"""Reuse document identity in the completion transaction; never parse a document."""
import json

from app.application.received_statements import ReceivedStatements
from app.application.statement_identity import account_signature, normalized


class _PostgresTransactionDb:
    """Queries on the caller's transaction; no auth, SQLite, pool or commits."""

    @staticmethod
    def execute(conn, query, params=()):
        return conn.execute(query.replace("?", "%s"), params)

    @classmethod
    def fetchone(cls, conn, query, params=()):
        with cls.execute(conn, query, params) as cursor:
            return cursor.fetchone()

    @classmethod
    def fetchall(cls, conn, query, params=()):
        with cls.execute(conn, query, params) as cursor:
            return cursor.fetchall()


def receive_in_transaction(repository, conn, record):
    # The shared service accepts the caller's connection and must not commit it.
    conn.execute(f'SET LOCAL search_path TO "{repository.schema}", public')
    ReceivedStatements(_PostgresTransactionDb()).receive(record, connection=conn)


def accounts_for_rows(repository, conn, scope, rows):
    receipt = conn.execute(f'''SELECT source_json FROM {repository.p}pro_received_statements
        WHERE organization_id=%s AND job_id=%s''', (scope.organization_id, scope.job_id)).fetchone()
    if not receipt:
        return [None] * len(rows)
    source = json.loads(receipt["source_json"])
    links = source.get("account_links", {})
    candidates = source.get("accounts") or [source]
    result = []
    for row in rows:
        evidence = row.get("statement_account") or {}
        matches = set()
        for candidate in candidates:
            account_id = links.get(account_signature(candidate))
            if not account_id:
                continue
            if evidence:
                # A bank account number alone is not a global identity. Match only
                # inside this identified source, requiring every supplied field.
                number = normalized(evidence.get("account_number"))
                if not number or number != normalized(candidate.get("account_number")):
                    continue
                if any(normalized(evidence.get(key)) and normalized(evidence[key]) != normalized(candidate.get(target))
                       for key, target in (("bank_branch", "branch"), ("bank_code", "bank_code"), ("cooperative", "cooperative"))):
                    continue
            elif len(candidates) != 1 or source.get("confidence") not in {"strong", "source_account"}:
                continue
            matches.add(account_id)
        result.append(next(iter(matches)) if len(matches) == 1 else None)
    return result
