from app.application.canonization import build_transaction_metadata
from app.application.ingestion import ingest_uploaded_document
from app.application.models import CanonicalTransaction, NormalizedTransaction
from app.application.normalization.transaction_normalizer import normalize_transactions
from app.application.parsers.service import ParsedDocument, ParsingService


def test_ingestion_builds_supported_document_contract() -> None:
    document = ingest_uploaded_document(
        filename="extrato.CSV",
        raw_bytes=b"date,description,amount\n2026-04-01,Pix recebido,10.00\n",
    )

    assert document.filename == "extrato.CSV"
    assert document.file_type == "csv"
    assert document.size_bytes == 54


def test_parsing_service_parses_csv_into_stage_result() -> None:
    document = ingest_uploaded_document(
        filename="extrato.csv",
        raw_bytes=b"date,description,amount\n2026-04-01,Pix recebido,10.00\n",
    )

    result = ParsingService().parse(document)

    assert result.file_type == "csv"
    assert result.transactions == [
        NormalizedTransaction(
            date="2026-04-01",
            description="Pix recebido",
            amount=10.0,
            type="inflow",
        )
    ]
    assert result.warning_types == [[]]
    assert result.running_balances == [None]


def test_normalization_package_exposes_transaction_normalizer() -> None:
    normalized = normalize_transactions(
        [
            NormalizedTransaction(
                date="01/04/2026",
                description=" pix recebido cliente ",
                amount=-300.0,
                type="",
            )
        ]
    )

    assert normalized[0] == NormalizedTransaction(
        date="2026-04-01",
        description="PIX RECEBIDO CLIENTE",
        amount=300.0,
        type="inflow",
    )


def test_canonization_aligns_parser_metadata_for_pipeline_rows() -> None:
    parsed = ParsedDocument(
        file_type="pdf",
        transactions=[
            NormalizedTransaction(date="2026-04-01", description="Compra", amount=-10.0, type="outflow"),
            NormalizedTransaction(date="2026-04-02", description="Pix", amount=20.0, type="inflow"),
        ],
        layout_inference_name="sample_layout",
        layout_inference_confidence=0.91,
        extracted_text="",
        parse_metrics={"selected_parser": "tabular"},
        canonical_transactions=[
            CanonicalTransaction(
                date="2026-04-01",
                description="Compra",
                amount=-10.0,
                type="outflow",
                running_balance=90.0,
                warnings=["balance_consistency_failed"],
            )
        ],
    )

    metadata = build_transaction_metadata(parsed)

    assert metadata.warning_types == [["balance_consistency_failed"], []]
    assert metadata.running_balances == [90.0, None]
