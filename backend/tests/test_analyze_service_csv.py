from app.application.analyze_service import AnalyzeService
from app.application.storage_service import TempAnalysisStorage


def test_analyze_service_uses_real_csv_content(tmp_path) -> None:
    storage = TempAnalysisStorage(root_dir=tmp_path, ttl_seconds=3600)
    service = AnalyzeService(storage=storage)
    raw = b"date,description,amount\n2026-04-01,IFOOD,-58.90\n2026-04-02,SALARIO,2500.00\n"

    result = service.analyze(filename="sample.csv", raw_bytes=raw)

    assert result.file_type == "csv"
    assert result.transactions_total == 2
    assert result.total_inflows == 2500.00
    assert result.total_outflows == -58.90
    assert result.net_total == 2441.10
    assert result.preview_transactions[0].description == "IFOOD"
    assert result.preview_transactions[1].description == "SALARIO"
    assert result.expires_at is not None
