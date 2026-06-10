from app.application.models import NormalizedTransaction
from app.application.normalization.canonical import build_canonical_transactions, from_normalized_transaction


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
    assert canonical.source_parser is None


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
        source_parser="grouped",
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
    assert canonical.source_parser == "grouped"
    assert canonical.running_balance == 512.75
    assert canonical.document_id == "doc-123"
    assert canonical.external_reference_id == "tx-abc"
    assert canonical.warnings == ["missing counterparty"]
    assert canonical.confidence == 0.91


def test_build_canonical_transactions_maps_row_metadata_without_layout_fallback_row_warning() -> None:
    class _ParsedRow:
        def __init__(
            self,
            transaction: NormalizedTransaction,
            *,
            source_page: int | None = None,
            source_line: int | None = None,
            running_balance: float | None = None,
            external_reference_id: str | None = None,
        ) -> None:
            self.transaction = transaction
            self.source_page = source_page
            self.source_line = source_line
            self.running_balance = running_balance
            self.external_reference_id = external_reference_id

    rows = [
        _ParsedRow(
            NormalizedTransaction(
                date="2026-05-03",
                description="TED ENTRADA",
                amount=500.0,
                type="inflow",
            ),
            source_page=1,
            source_line=12,
            running_balance=1500.0,
            external_reference_id="abc-123",
        )
    ]

    canonical_transactions = build_canonical_transactions(
        rows,
        bank_name="Banco Exemplo",
        layout_name="layout_example_v1",
        layout_used_fallback=True,
        layout_confidence=0.88,
        source_parser="tabular",
    )

    assert len(canonical_transactions) == 1
    canonical = canonical_transactions[0]
    assert canonical.bank_name == "Banco Exemplo"
    assert canonical.layout_name == "layout_example_v1"
    assert canonical.source_page == 1
    assert canonical.source_line == 12
    assert canonical.source_parser == "tabular"
    assert canonical.running_balance == 1500.0
    assert canonical.external_reference_id == "abc-123"
    assert canonical.confidence == 0.88
    assert canonical.warnings == []
