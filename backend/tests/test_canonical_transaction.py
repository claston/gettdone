from app.application.models import NormalizedTransaction
from app.application.normalization.canonical import from_normalized_transaction


def test_from_normalized_transaction_preserves_core_fields() -> None:
    normalized = NormalizedTransaction(
        date="2026-05-01",
        description="PIX RECEBIDO",
        amount=100.25,
        type="inflow",
    )

    canonical = from_normalized_transaction(normalized)

    assert canonical.date == "2026-05-01"
    assert canonical.description == "PIX RECEBIDO"
    assert canonical.amount == 100.25
    assert canonical.type == "inflow"


def test_from_normalized_transaction_sets_optional_metadata() -> None:
    normalized = NormalizedTransaction(
        date="2026-05-02",
        description="PAGAMENTO CARTAO",
        amount=-42.0,
        type="outflow",
    )

    canonical = from_normalized_transaction(
        normalized,
        bank_name="Nubank",
        layout_name="nubank_statement_ptbr",
        source_page=3,
        source_line=18,
        running_balance=512.75,
        document_id="doc-123",
        external_reference_id="tx-abc",
        warnings=["missing counterparty"],
        confidence=0.91,
    )

    assert canonical.bank_name == "Nubank"
    assert canonical.layout_name == "nubank_statement_ptbr"
    assert canonical.source_page == 3
    assert canonical.source_line == 18
    assert canonical.running_balance == 512.75
    assert canonical.document_id == "doc-123"
    assert canonical.external_reference_id == "tx-abc"
    assert canonical.warnings == ["missing counterparty"]
    assert canonical.confidence == 0.91
