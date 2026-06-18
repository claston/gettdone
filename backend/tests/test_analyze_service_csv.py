from uuid import uuid4

from app.application.analysis_response_builder import build_analyze_response, persist_conversion_result
from app.application.default_conversion_pipeline import build_default_conversion_pipeline
from app.application.storage_service import TempAnalysisStorage


def _run_pipeline_preview(*, storage: TempAnalysisStorage, filename: str, raw_bytes: bytes):
    pipeline_result = build_default_conversion_pipeline().run(
        filename=filename,
        raw_bytes=raw_bytes,
        analysis_id=f"an_{uuid4().hex[:12]}",
    )
    persisted_result = persist_conversion_result(storage=storage, pipeline_result=pipeline_result)
    return build_analyze_response(persisted_result=persisted_result)


def test_analyze_service_uses_real_csv_content(tmp_path) -> None:
    storage = TempAnalysisStorage(root_dir=tmp_path, ttl_seconds=3600)
    raw = b"date,description,amount\n2026-04-01,IFOOD,-58.90\n2026-04-02,SALARIO,2500.00\n"

    result = _run_pipeline_preview(storage=storage, filename="sample.csv", raw_bytes=raw)

    assert result.file_type == "csv"
    assert result.transactions_total == 2
    assert result.total_inflows == 2500.00
    assert result.total_outflows == -58.90
    assert result.net_total == 2441.10
    assert result.operational_summary.total_volume == 2558.9
    assert result.operational_summary.inflow_count == 1
    assert result.operational_summary.outflow_count == 1
    assert result.operational_summary.reconciled_entries == 0
    assert result.operational_summary.unmatched_entries == 2
    assert result.preview_transactions[0].description == "IFOOD"
    assert result.preview_transactions[1].description == "SALARIO"
    assert result.expires_at is not None


def test_analyze_service_detects_internal_transfer_match(tmp_path) -> None:
    storage = TempAnalysisStorage(root_dir=tmp_path, ttl_seconds=3600)
    raw = (
        b"date,description,amount\n"
        b"2026-04-02,Transferencia enviada PIX,-350.75\n"
        b"2026-04-02,Transferencia recebida PIX,350.75\n"
    )

    result = _run_pipeline_preview(storage=storage, filename="sample.csv", raw_bytes=raw)

    assert result.reconciliation.matched_groups == 1
    assert result.reconciliation.reversed_entries == 0
    assert result.operational_summary.reconciled_entries == 2
    assert result.operational_summary.unmatched_entries == 0
    statuses = [row.reconciliation_status for row in result.preview_transactions]
    assert statuses == ["matched_transfer", "matched_transfer"]


def test_analyze_service_detects_reversal_pair(tmp_path) -> None:
    storage = TempAnalysisStorage(root_dir=tmp_path, ttl_seconds=3600)
    raw = (
        b"date,description,amount\n"
        b"2026-04-02,Compra Mercado,-120.00\n"
        b"2026-04-03,Estorno Compra Mercado,120.00\n"
    )

    result = _run_pipeline_preview(storage=storage, filename="sample.csv", raw_bytes=raw)

    assert result.reconciliation.matched_groups == 0
    assert result.reconciliation.reversed_entries == 2
    assert result.operational_summary.reconciled_entries == 2
    assert result.operational_summary.unmatched_entries == 0
    statuses = [row.reconciliation_status for row in result.preview_transactions]
    assert statuses == ["reversed", "reversed"]


def test_analyze_service_applies_single_normalizer_rules(tmp_path) -> None:
    storage = TempAnalysisStorage(root_dir=tmp_path, ttl_seconds=3600)
    raw = (
        b"date,description,amount,type\n"
        b"2026-04-01,  ifood   sao paulo  ,-58.90,debito\n"
        b"2026-04-02,salario,-2500.00,credito\n"
    )

    result = _run_pipeline_preview(storage=storage, filename="sample.csv", raw_bytes=raw)

    assert result.total_inflows == 2500.00
    assert result.total_outflows == -58.90
    assert result.net_total == 2441.10
    assert result.preview_transactions[0].description == "IFOOD"
    assert result.preview_transactions[1].description == "SALARIO"
    assert result.preview_transactions[1].amount == 2500.00
    assert result.preview_before_after[0].description_before == "ifood   sao paulo"
    assert result.preview_before_after[0].description_after == "IFOOD"
    assert result.preview_before_after[1].amount_before == -2500.00
    assert result.preview_before_after[1].amount_after == 2500.00


def test_analyze_service_detects_possible_duplicate_group(tmp_path) -> None:
    storage = TempAnalysisStorage(root_dir=tmp_path, ttl_seconds=3600)
    raw = (
        b"date,description,amount\n"
        b"2026-04-10,Compra mercado central,-120.00\n"
        b"2026-04-11,Compra mercado central loja 1,-120.00\n"
    )

    result = _run_pipeline_preview(storage=storage, filename="sample.csv", raw_bytes=raw)

    assert result.reconciliation.potential_duplicates == 1
    assert result.operational_summary.reconciled_entries == 2
    assert result.operational_summary.unmatched_entries == 0
    statuses = [row.reconciliation_status for row in result.preview_transactions]
    assert statuses == ["grouped", "grouped"]
