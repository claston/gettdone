"""Real PostgreSQL isolation, allocations, expiry and revision tests."""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb
from test_canonical_movements_postgres import DSN
from test_financial_center import add_document, adopt
from test_pro_fiscal_documents import ISSUER, RECIPIENT, nfe, nfse

from app.application.errors import AnalysisEditConflictError, AnalysisNotFoundError
from app.application.pro_fiscal_documents import parse_fiscal_xml
from app.application.pro_fiscal_service import ProFiscalService
from tests.test_canonical_pro import database as paid_database

database = paid_database
pytestmark = pytest.mark.skipif(not DSN, reason="Requires disposable PostgreSQL")


def setup(database):
    repo, scope, *_ = database
    initial, _ = adopt(database)
    with repo._connect() as conn:
        conn.execute(f"UPDATE {repo.p}pro_clients SET tax_id=%s WHERE id=%s", (ISSUER, scope.client_id))
    return ProFiscalService(repo), scope, initial["movement_ids"]["stable"]


def insert_note(service, scope, amount="10.00"):
    payload = parse_fiscal_xml(nfe(value=amount))
    identifier = uuid4()
    payload["number"] = str(identifier.int)[:10]
    with service.repo._connect() as conn:
        conn.execute(
            f"""INSERT INTO {service.repo.p}pro_fiscal_documents
            (id,organization_id,client_id,payload,expires_at) VALUES (%s,%s,%s,%s,%s)""",
            (identifier, scope.organization_id, scope.client_id, Jsonb(payload), datetime.now(timezone.utc) + timedelta(hours=1)),
        )
    return str(identifier)


def test_classification_is_versioned_and_does_not_modify_bank_values(database):
    service, scope, movement = setup(database)
    args = dict(category="operating_receipt", movement_version=1, expected_version=0, operation_id="classification")
    first = service.classify(scope.actor_id, scope.client_id, movement, **args)
    assert service.classify(scope.actor_id, scope.client_id, movement, **args) == first
    month = service.workspace(scope.actor_id, scope.client_id, month="2026-04")
    assert month["summary"]["operating_receipts"] == "10.00"
    assert month["movements"][0]["amount"] == "10.00"
    second = service.classify(
        scope.actor_id, scope.client_id, movement, **{**args, "expected_version": 1, "category": "capital", "operation_id": "changed"}
    )
    assert second["before"]["category"] == "operating_receipt"
    assert service.workspace(scope.actor_id, scope.client_id, month="2026-04")["summary"]["operating_receipts"] == "0.00"


def test_partial_allocations_reserve_amounts_and_undo_keeps_audit(database):
    service, scope, movement = setup(database)
    note, other = insert_note(service, scope), insert_note(service, scope)
    args = dict(amount="6.00", document_version=1, movement_version=1, operation_id="link")
    link = service.link(scope.actor_id, scope.client_id, note, movement, **args)
    assert service.link(scope.actor_id, scope.client_id, note, movement, **args) == link
    with pytest.raises(ValueError, match="saldo"):
        service.link(scope.actor_id, scope.client_id, other, movement, **{**args, "operation_id": "over"})
    service.unlink(scope.actor_id, scope.client_id, link["id"], expected_version=1, operation_id="undo")
    service.link(scope.actor_id, scope.client_id, other, movement, **{**args, "operation_id": "after-undo"})
    with service.repo._connect() as conn:
        assert conn.execute(f"SELECT count(*) AS n FROM {service.repo.p}pro_fiscal_operations").fetchone()["n"] == 3


def test_revised_movement_invalidates_classification_and_link(database):
    service, scope, movement = setup(database)
    note = insert_note(service, scope)
    service.classify(
        scope.actor_id,
        scope.client_id,
        movement,
        category="operating_receipt",
        movement_version=1,
        expected_version=0,
        operation_id="classify",
    )
    service.link(
        scope.actor_id, scope.client_id, note, movement, amount="10.00", document_version=1, movement_version=1, operation_id="link"
    )
    service.repo.apply(
        scope,
        [{"movement_id": movement, "date": "2026-04-01", "description": "Corrigido", "credit": 11}],
        expected_version=1,
        operation_id="edit",
        channel="pro",
    )
    result = service.workspace(scope.actor_id, scope.client_id, month="2026-04")
    assert result["movements"][0]["classification"]["state"] == "review_required"
    assert result["links"][0]["state"] == "review_required"
    assert result["summary"]["operating_receipts"] == "0.00"


def test_concurrent_links_cannot_double_allocate(database):
    service, scope, movement = setup(database)
    notes = [insert_note(service, scope), insert_note(service, scope)]

    def link(note):
        try:
            return service.link(
                scope.actor_id, scope.client_id, note, movement, amount="10.00", document_version=1, movement_version=1, operation_id=note
            )
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(link, notes))
    assert sum(result is not None for result in results) == 1


def test_cross_owner_expiry_and_document_version_are_checked(database):
    service, scope, movement = setup(database)
    note = insert_note(service, scope)
    with pytest.raises(Exception):
        service.workspace("other-owner", scope.client_id, month="2026-04")
    with pytest.raises(AnalysisEditConflictError):
        service.link(
            scope.actor_id, scope.client_id, note, movement, amount="10.00", document_version=2, movement_version=1, operation_id="stale"
        )
    with service.repo._connect() as conn:
        conn.execute(f"UPDATE {service.repo.p}pro_fiscal_documents SET expires_at=clock_timestamp()-interval '1 second'")
    with pytest.raises(AnalysisNotFoundError):
        service.link(
            scope.actor_id, scope.client_id, note, movement, amount="10.00", document_version=1, movement_version=1, operation_id="expired"
        )


def test_month_total_includes_rows_beyond_first_page(database):
    service, scope, _ = setup(database)
    repo, _, original, *_ = database
    rows = [dict(original["preview_transactions"][0], transaction_id=f"r-{i}", date="2026-05-01") for i in range(60)]
    adopt(database, *add_document(database, suffix="fiscal-many", rows=rows))
    result = service.workspace(scope.actor_id, scope.client_id, month="2026-05")
    assert len(result["movements"]) == 50
    assert result["summary"]["unclassified_inflows"] == "600.00"
    assert result["next_offset"] == 50


class Objects:
    def __init__(self):
        self.data = {}

    def reference(self, filename, size, digest):
        return {
            "storage_key": "doc_" + uuid4().hex[:24],
            "filename": filename,
            "file_type": "xml",
            "size_bytes": size,
            "sha256_hex": digest,
        }

    def prepare(self, reference):
        return {"url": "https://example.test/upload", "fields": {}}

    def verify(self, reference):
        pass

    def read(self, reference):
        return self.data[reference["sha256_hex"]]

    def delete(self, reference):
        self.data.pop(reference["sha256_hex"], None)


class Queue:
    def __init__(self):
        self.messages = []

    def publish(self, identifier):
        self.messages.append(identifier)


def test_async_import_idempotency_routing_and_cleanup(database):
    service, scope, _ = setup(database)
    service.objects, service.queue = Objects(), Queue()
    raw = nfe()
    digest = hashlib.sha256(raw).hexdigest()
    args = dict(filename="nota.xml", size_bytes=len(raw), digest=digest)
    imported = service.prepare_import(scope.actor_id, scope.client_id, **args)
    assert service.prepare_import(scope.actor_id, scope.client_id, **args)["id"] == imported["id"]
    service.objects.data[digest] = raw
    service.submit_import(scope.actor_id, scope.client_id, imported["id"])
    assert service.queue.messages == [imported["id"]]
    service.complete_import(imported["id"], lambda data, kind: parse_fiscal_xml(data))
    result = service.workspace(scope.actor_id, scope.client_id, month="2026-11")
    assert result["documents"][0]["direction"] == "issued"
    assert result["summary"]["documented_sales"] == "100.00"
    service.complete_import(imported["id"], lambda *_: pytest.fail("must not reparse"))
    with service.repo._connect() as conn:
        conn.execute(f"UPDATE {service.repo.p}pro_fiscal_imports SET expires_at=clock_timestamp()-interval '1 second'")
        conn.execute(f"UPDATE {service.repo.p}pro_fiscal_documents SET expires_at=clock_timestamp()-interval '1 second'")
    assert service.cleanup() == 1
    assert not service.objects.data


def test_company_identity_change_requires_link_review(database):
    service, scope, movement = setup(database)
    note = insert_note(service, scope)
    service.link(
        scope.actor_id,
        scope.client_id,
        note,
        movement,
        amount="10.00",
        document_version=1,
        movement_version=1,
        operation_id="identity-link",
    )
    with service.repo._connect() as conn:
        conn.execute(f"UPDATE {service.repo.p}pro_clients SET tax_id=%s WHERE id=%s", (RECIPIENT, scope.client_id))
    assert service.workspace(scope.actor_id, scope.client_id, month="2026-04")["links"][0]["state"] == "review_required"


def test_reviewed_pdf_without_series_still_requires_duplicate_resolution(database):
    service, scope, movement = setup(database)
    first, duplicate = insert_note(service, scope), insert_note(service, scope)
    with service.repo._connect() as conn:
        for identifier, number, series in ((first, "123", "1"), (duplicate, "000123", None)):
            conn.execute(
                f"UPDATE {service.repo.p}pro_fiscal_documents SET payload=payload || %s WHERE id=%s",
                (Jsonb({"number": number, "series": series}), identifier),
            )
    assert service.workspace(scope.actor_id, scope.client_id, month="2026-11")["summary"]["documented_sales"] == "0.00"
    with pytest.raises(ValueError, match="duplicata"):
        service.link(
            scope.actor_id,
            scope.client_id,
            first,
            movement,
            amount="10.00",
            document_version=1,
            movement_version=1,
            operation_id="duplicate-block",
        )
    service.review_document(
        scope.actor_id, scope.client_id, duplicate, expected_version=1, operation_id="resolve-duplicate", changes={"status": "duplicate"}
    )
    assert service.workspace(scope.actor_id, scope.client_id, month="2026-11")["summary"]["documented_sales"] == "10.00"


def test_same_identity_does_not_extend_retention_and_replacement_preserves_source(database):
    service, scope, _ = setup(database)
    service.objects, service.queue = Objects(), Queue()

    def ingest(raw):
        digest = hashlib.sha256(raw).hexdigest()
        service.objects.data[digest] = raw
        imported = service.prepare_import(scope.actor_id, scope.client_id, filename="nota.xml", size_bytes=len(raw), digest=digest)
        service.submit_import(scope.actor_id, scope.client_id, imported["id"])
        service.complete_import(imported["id"], lambda data, kind: parse_fiscal_xml(data))
        return imported["id"]

    first = ingest(nfse())
    with service.repo._connect() as conn:
        original = conn.execute(f"SELECT * FROM {service.repo.p}pro_fiscal_documents").fetchone()
    alias = ingest(nfse() + b"\n")
    with service.repo._connect() as conn:
        retained = conn.execute(f"SELECT expires_at FROM {service.repo.p}pro_fiscal_imports WHERE id=%s", (alias,)).fetchone()
        assert retained["expires_at"] == original["expires_at"]
    replacement = nfse().replace(b'Id="NFS' + b"1" * 50, b'Id="NFS' + b"2" * 50).replace(b"<nNFSe>7", b"<nNFSe>8")
    replacement = replacement.replace(b"</infDPS>", b"<subst><chSubstda>" + b"1" * 50 + b"</chSubstda></subst></infDPS>")
    ingest(replacement)
    result = service.workspace(scope.actor_id, scope.client_id, month="2026-11")
    assert result["summary"]["documented_sales"] == "100.00"
    assert next(n for n in result["documents"] if n["id"] == str(original["id"]))["status"] == "substituted"
    with service.repo._connect() as conn:
        baseline = conn.execute(f"SELECT extraction FROM {service.repo.p}pro_fiscal_imports WHERE id=%s", (first,)).fetchone()
        assert baseline["extraction"]["status"] == "reported"
