from app.application.conversion.uploaded_document import ingest_uploaded_document
from app.application.conversion_pipeline import ConversionPipeline
from app.application.models import NormalizedTransaction
from app.application.parsers.service import ParsingService


def test_conversion_pipeline_builds_analysis_data_without_storage() -> None:
    raw = b"date,description,amount\n2026-04-01,PIX recebido cliente,300.00\n"

    result = ConversionPipeline().run(
        filename="extrato.csv",
        raw_bytes=raw,
        analysis_id="an_test_pipeline",
    )

    assert result.analysis_data.analysis_id == "an_test_pipeline"
    assert result.analysis_data.file_type == "csv"
    assert result.analysis_data.transactions_total == 1
    assert result.analysis_data.total_inflows == 300.0
    assert result.analysis_data.preview_transactions[0].description == "PIX RECEBIDO CLIENTE"
    assert result.operational_summary.reconciled_entries == 0
    assert result.top_expenses_rows == []


def test_conversion_pipeline_preserves_before_after_rows() -> None:
    raw = b"date,description,amount,type\n2026-04-01, salario ,-2500.00,credito\n"

    result = ConversionPipeline().run(
        filename="extrato.csv",
        raw_bytes=raw,
        analysis_id="an_test_before_after",
    )

    assert result.analysis_data.preview_before_after[0].description_before == "salario"
    assert result.analysis_data.preview_before_after[0].description_after == "SALARIO"
    assert result.analysis_data.preview_before_after[0].amount_before == -2500.0
    assert result.analysis_data.preview_before_after[0].amount_after == 2500.0


def test_conversion_pipeline_accepts_custom_reconciliation_hook() -> None:
    def reconcile_all(transactions: list[NormalizedTransaction]):
        class Result:
            statuses = ["matched_custom" for _ in transactions]
            matched_groups = len(transactions)
            reversed_entries = 0
            potential_duplicates = 0

        return Result()

    result = ConversionPipeline(reconcile_transactions=reconcile_all).run(
        filename="extrato.csv",
        raw_bytes=b"date,description,amount\n2026-04-01,Pix recebido,10.00\n",
        analysis_id="an_test_hook",
    )

    assert result.analysis_data.preview_transactions[0].reconciliation_status == "matched_custom"
    assert result.operational_summary.reconciled_entries == 1


def test_conversion_pipeline_builds_analysis_data_from_parsed_document() -> None:
    raw = b"date,description,amount\n2026-04-01,PIX recebido cliente,300.00\n"
    document = ingest_uploaded_document(filename="extrato.csv", raw_bytes=raw)
    parsed_document = ParsingService().parse(document)

    result = ConversionPipeline().run_parsed_document(
        document=document,
        parsed_document=parsed_document,
        analysis_id="an_test_pipeline_parsed",
        parse_ms=1.5,
    )

    assert result.analysis_data.analysis_id == "an_test_pipeline_parsed"
    assert result.analysis_data.file_type == "csv"
    assert result.analysis_data.transactions_total == 1
    assert result.parse_ms == 1.5
