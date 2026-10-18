from app.application.conversion.uploaded_document import ingest_uploaded_document
from app.application.conversion_pipeline import ConversionPipeline
from app.application.models import CanonicalTransaction, NormalizedTransaction
from app.application.parsers.service import ParsingService


def test_new_source_order_is_physical_and_independent_from_accounting_order(tmp_path) -> None:
    from dataclasses import replace

    from app.application.storage_service import TempAnalysisStorage

    document = ingest_uploaded_document(filename="extrato.csv", raw_bytes=(
        b"date,description,amount\n2026-04-29,TERCEIRO,5\n2026-04-30,SEGUNDO,10\n2026-04-30,PRIMEIRO,-2\n"
    ))
    parsed = ParsingService().parse(document)
    parsed = replace(parsed, canonical_transactions=[
        CanonicalTransaction(date=row.date, description=row.description, amount=row.amount, type=row.type,
                             source_page=page, source_line=line)
        for row, page, line in zip(parsed.transactions, [2, 1, 1], [1, 20, 10], strict=True)
    ])
    result = ConversionPipeline().run_parsed_document(document=document, parsed_document=parsed,
                                                     analysis_id="an_source_order", parse_ms=1)
    rows = result.analysis_data.preview_transactions
    assert [row.source_sequence for row in rows] == [3, 2, 1]
    assert [row.description for row in rows] == ["TERCEIRO", "SEGUNDO", "PRIMEIRO"]
    assert [row.source_line for row in rows] == [1, 20, 10]
    original_ids = [row.transaction_id for row in rows]
    assert len(set(original_ids)) == 3
    storage = TempAnalysisStorage(root_dir=tmp_path)
    storage.save_analysis(result.analysis_data)
    edited = storage.apply_convert_edits("an_source_order", [{
        "row_id": original_ids[2], "date": "2026-04-01", "description": "EDITADO", "debit": 2,
    }])
    assert [row["transaction_id"] for row in edited["preview_transactions"]] == original_ids
    assert [row["source_sequence"] for row in edited["preview_transactions"]] == [3, 2, 1]
    anchor = original_ids[2]
    for number in range(2):
        inserted = storage.apply_convert_edits("an_source_order", [{
            "action": "insert", "transaction_id": f"manual-{number}", "anchor_transaction_id": anchor,
            "date": "2026-04-30", "description": "MANUAL", "credit": 8,
        }])
    manual = [row for row in inserted["preview_transactions"] if row["transaction_id"].startswith("manual-")]
    assert [(row["source_sequence"], row["source_subsequence"]) for row in manual] == [(1, 1), (1, 2)]
    assert all(row["source_page"] is None for row in manual)
    deleted = storage.apply_convert_edits("an_source_order", [{"action": "delete", "row_id": anchor}])
    assert next(row for row in deleted["preview_transactions"] if row["transaction_id"] == anchor)["is_deleted"]
    restored = storage.apply_convert_edits("an_source_order", [{
        "action": "restore", "row_id": anchor, "date": "2026-04-30", "description": "RESTAURADO", "debit": 2,
    }])
    assert next(row for row in restored["preview_transactions"] if row["transaction_id"] == anchor)["source_sequence"] == 1


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


def test_source_pages_follow_retained_rows_and_survive_storage_edits(tmp_path) -> None:
    from app.application.conversion.contracts.payloads import TransactionPreview
    from app.application.storage_service import TempAnalysisStorage

    document = ingest_uploaded_document(
        filename="extrato.csv",
        raw_bytes=b"date,description,amount\n2026-04-01,Saldo,100\n2026-04-02,Pix,20\n",
    )
    parsed = ParsingService().parse(document)
    from dataclasses import replace

    parsed = replace(parsed, canonical_transactions=[
        CanonicalTransaction(date=row.date, description=row.description, amount=row.amount, type=row.type, source_page=page)
        for row, page in zip(parsed.transactions, [1, 3], strict=True)
    ])
    result = ConversionPipeline(is_balance_metadata_row=lambda _rows, idx: idx == 0).run_parsed_document(
        document=document, parsed_document=parsed, analysis_id="an_pages", parse_ms=1,
    )
    row = result.analysis_data.preview_transactions[0]
    assert row.source_page == 3
    assert TransactionPreview(**vars(row)).source_page == 3
    storage = TempAnalysisStorage(root_dir=tmp_path, ttl_seconds=3600)
    storage.save_analysis(result.analysis_data)
    saved = storage.apply_convert_edits("an_pages", [
        {"action": "update", "row_id": "row_1", "date": row.date, "description": "PIX EDITADO", "credit": 25},
        {"action": "insert", "insert_position": 0, "date": row.date, "description": "MANUAL", "debit": 5},
    ])
    assert [item["source_page"] for item in saved["preview_transactions"]] == [None, 3]
