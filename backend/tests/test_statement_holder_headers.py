"""Anonymized header shapes; never store users' PDFs or real account identities."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.application.received_statements import ReceivedStatements
from app.application.statement_identity import identify_statement
from tests import test_received_statements as receipt_fixtures

receipts = receipt_fixtures.receipts
record = receipt_fixtures.record

HEADERS = [
    ("Cooperativa: 0123-4 / COOPERATIVA DE CREDITOConta: 12.345-6 / COMERCIO AURORA LTDAPeriodo: 01/04/2026 - 30/04/2026",
     "756", "COMERCIO AURORA LTDA", "0123-4", "12.345-6"),
    ("Associado: MERCADO HORIZONTE LTDA\nCooperativa:0007\nConta:00987-6\nExtrato(Periodo de 01/07/2026 a 31/07/2026)",
     "748", "MERCADO HORIZONTE LTDA", "0007", "00987-6"),
]
WRAPPED_HEADER = (
    "SICOOB\nEXTRATO DE CONTA CORRENTE\n04/05/2026 - 11:00:38\n"
    "Cooperativa:\n0123-4 / COOPERATIVA DE CREDITO\nConta:\n12.345-6 / COMERCIO AURORA LTDA\n"
    "Periodo:\n01/04/2026 - 30/04/2026\nHISTORICO DE MOVIMENTACAO",
    *HEADERS[0][1:],
)


@pytest.mark.parametrize("representation", ["native", "ocr"])
@pytest.mark.parametrize("header,bank,name,cooperative,account", [*HEADERS, WRAPPED_HEADER])
def test_labeled_holder_and_cooperative_account_without_tax(header, bank, name, cooperative, account, representation):
    pages = [header + "\nData Descricao Valor\n01/04/2026 PIX 98.765.432/0001-10 TERCEIRO 10,00"]
    source = identify_statement({"bank_code": bank}, page_texts=pages, representation=representation)
    assert source["holder_name"] == name
    assert source["holder_tax_id"] == ""
    assert source["cooperative"] == cooperative and source["account_number"] == account
    assert source["branch"] == ""  # A cooperative label is not a printed agency.
    assert source["confidence"] == "source_account"
    assert source["references"] and source["representation"] == representation


@pytest.mark.parametrize("table_header", ["Data Descricao Valor", "Data\nDescricao\nValor", "01/04/2026"])
def test_holder_labels_inside_movements_are_not_header_evidence(table_header):
    source = identify_statement({"bank_code": "748"}, page_texts=[
        f"Extrato de conta corrente\n{table_header}\n01/04/2026 PIX 10,00\n"
        "Associado: CONTRAPARTE LTDA\nCooperativa:0007\nConta:00987-6\nCPF/CNPJ do titular: 98.765.432/0001-10",
    ])
    assert source["holder_name"] == source["holder_tax_id"] == ""
    assert source["confidence"] == "missing"


def test_distinct_holders_stay_ambiguous_and_name_only_cannot_create_customer(receipts):
    service, owner, _ = receipts
    header, bank, *_ = HEADERS[1]
    source = identify_statement({"bank_code": bank}, page_texts=[header, header.replace("HORIZONTE", "OUTRO")])
    assert source["confidence"] == "ambiguous"
    service.receive(record(owner, source=source, days=30))
    source = identify_statement({"bank_code": bank}, page_texts=["Associado: MERCADO HORIZONTE LTDA"])
    service.receive(record(owner, "name_only", source=source, days=30))
    assert service.dashboard(owner)["clients"] == []


@pytest.mark.parametrize("header,bank,name,cooperative,account", [*HEADERS, WRAPPED_HEADER])
def test_new_document_client_without_tax_is_reused_only_by_complete_account(receipts, header, bank, name, cooperative, account):
    service, owner, other = receipts
    source = identify_statement({"bank_code": bank}, page_texts=[header])
    service.receive(record(owner, "first", source=source, days=30))
    # Punctuation differences do not change the source account identity.
    repeated = identify_statement({"bank_code": bank}, page_texts=[header.replace(account, account.replace(".", ""))])
    service.receive(record(owner, "second", "batch_b", source=repeated, days=30))
    page = service.dashboard(owner)
    assert len(page["clients"]) == 1 and page["pending"] == []
    client = page["clients"][0]
    assert client["name"] == name and client["tax_id"] is None
    assert client["historical_count"] == 2 and len(client["accounts"]) == 1
    account_record = service.catalog(owner)["accounts"][0]
    assert account_record["branch"] == "" and cooperative in account_record["name"]
    # Same name/account at another cooperative is a separate documentary identity.
    separate = identify_statement({"bank_code": bank}, page_texts=[header.replace(cooperative, "0999")])
    service.receive(record(owner, "different_cooperative", source=separate, days=30))
    assert len(service.dashboard(owner)["clients"]) == 2
    assert service.dashboard(other)["clients"] == []


def test_ambiguous_existing_account_links_are_not_silently_chosen(receipts):
    service, owner, _ = receipts
    header, bank, *_ = HEADERS[1]
    source = identify_statement({"bank_code": bank}, page_texts=[header])
    service.receive(record(owner, "first", source=source, days=30))
    duplicate = service.save_client(owner, name="Outro titular")
    service.receive(record(owner, "second", source=source, days=30))
    service.save_context(owner, job_id="second", client_id=duplicate["id"], account_id=None, period_ids=[], expected_version=1)
    # The association is explicit and contradictory: do not attach another file by inference.
    service.receive(record(owner, "third", source=source, days=30))
    assert any(row["job_id"] == "third" for row in service.dashboard(owner)["pending"])


def test_refresh_upgrades_existing_native_pdf_evidence_once_without_touching_result_or_ttl(receipts):
    from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf

    service, owner, _ = receipts
    header, bank, *_ = HEADERS[1]
    raw = generate_native_text_pdf((tuple(header.splitlines()),))
    legacy = {"version": 1, "holder_name": "", "holder_tax_id": "", "bank_code": bank,
              "branch": "", "account_number": "00987-6", "confidence": "missing"}
    item = record(owner, source=legacy, days=30)
    service.receive(item)
    payload_before = dict(item.result.payload)
    expiry = item.expires_at
    loads = []

    class Repository:
        def list_files_for_owner(self, **kwargs):
            assert kwargs["identity_id"] == owner
            return [SimpleNamespace(job_id=item.job.job_id, created_at=item.created_at)]

        def get(self, job_id):
            return item

    class Documents:
        def load(self, reference):
            loads.append(reference.filename)
            return SimpleNamespace(raw_bytes=raw)

    service = ReceivedStatements(service.db, Repository(), document_store=Documents())
    service.sync(owner)
    assert len(service.dashboard(owner)["clients"]) == 1
    service.sync(owner)
    assert len(loads) == 1
    assert item.result.payload == payload_before and item.expires_at == expiry


def test_expired_pending_pdf_is_not_loaded_for_recovery(receipts):
    service, owner, _ = receipts
    item = record(owner, days=30, source={"version": 1, "bank_code": "748"})
    item.expires_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
    class Repository:
        def list_files_for_owner(self, **kwargs):
            return [SimpleNamespace(job_id=item.job.job_id, created_at=item.created_at)]

        def get(self, job_id):
            return item

    service = ReceivedStatements(service.db, Repository(), document_store=SimpleNamespace(load=lambda _: pytest.fail("Expired PDF read")))
    service.sync(owner)
    assert service.dashboard(owner)["clients"] == []


def test_two_cooperatives_with_same_number_keep_distinct_account_links(receipts):
    service, owner, _ = receipts
    header, bank, *_ = HEADERS[1]
    source = identify_statement({"bank_code": bank, "financial_document": {"metadata": {
        "owner_name": "MERCADO HORIZONTE LTDA", "owner_tax_id": "12345678000190"}}},
        page_texts=[header, header.replace("0007", "0008")])
    for job in ("first", "second"):
        service.receive(record(owner, job, source=source, days=30))
    client = service.dashboard(owner)["clients"][0]
    assert client["historical_count"] == 2 and len(client["accounts"]) == 2
    assert all(account["statement_count"] == 2 for account in client["accounts"])
    assert {row["name"] for row in service.catalog(owner)["accounts"]} == {
        "Conta de origem · Cooperativa 0007", "Conta de origem · Cooperativa 0008"}


@pytest.mark.parametrize("content", [b"not a PDF", b""])
def test_native_recovery_failure_preserves_result_and_pending_identity(receipts, content):
    service, owner, _ = receipts
    item = record(owner, source={"version": 1, "bank_code": "748", "account_number": "00987-6"}, days=30)
    before = dict(item.result.payload)
    service.document_store = SimpleNamespace(load=lambda _: SimpleNamespace(raw_bytes=content))
    assert service._recover_native_source(owner, item) is None
    service.receive(item)
    assert item.result.payload == before and service.dashboard(owner)["clients"] == []


def test_refresh_never_reads_or_reassigns_a_confirmed_document(receipts):
    service, owner, _ = receipts
    source = {"version": 1, "bank_code": "748", "account_number": "00987-6", "holder_name": "Nome parcial"}
    item = record(owner, source=source, days=30)
    service.receive(item)
    service.confirm(owner, item.job.job_id, name="Titular conferido", expected_version=0)
    service.document_store = SimpleNamespace(load=lambda _: pytest.fail("Confirmed PDF read"))
    assert service._recover_native_source(owner, item) is None
    service.receive(item)
    assert service.dashboard(owner)["clients"][0]["name"] == "Titular conferido"
