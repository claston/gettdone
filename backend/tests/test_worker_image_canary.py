import pytest

from app.workers import worker_image_canary as canary


@pytest.mark.parametrize("enabled,event", [(None, {"contract": "worker-image-v1"}), ("1", {}), ("1", {"file": "customer.pdf"})])
def test_canary_rejects_unapproved_invocations(monkeypatch, enabled, event):
    monkeypatch.delenv("WORKER_IMAGE_CANARY", raising=False)
    if enabled:
        monkeypatch.setenv("WORKER_IMAGE_CANARY", enabled)
    monkeypatch.setattr(canary, "run_checks", lambda: pytest.fail("must reject before running checks"))
    with pytest.raises(ValueError, match="synthetic canary"):
        canary.lambda_handler(event, None)


@pytest.mark.parametrize("url", ["postgresql://worker_canary:password@production/worker_canary", "postgresql://admin:p@localhost/real_data"])
def test_canary_rejects_non_synthetic_database(url):
    with pytest.raises(ValueError, match="local synthetic database"):
        canary.check_postgres(url)


def test_synthetic_conversions_preserve_amounts_and_exports(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_RUNTIME_TMP_DIR", str(tmp_path))
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    monkeypatch.delenv("WORKER_CANARY_DATABASE_URL", raising=False)
    result = canary.run_checks()
    assert result["status"] == "passed"
    assert result["checks"] == ["csv", "xlsx", "ofx", "pdf", "pdfium", "psycopg"]
    assert result["postgres"] == "not_configured"


def test_canary_runs_runtime_boundary_before_checks(monkeypatch):
    calls = []
    monkeypatch.setenv("WORKER_IMAGE_CANARY", "1")
    monkeypatch.setattr(canary, "verify_worker_image", lambda: calls.append("boundary"))
    monkeypatch.setattr(canary, "run_checks", lambda: calls.append("checks") or {"status": "passed"})
    assert canary.lambda_handler({"contract": "worker-image-v1"}, None) == {"status": "passed"}
    assert calls == ["boundary", "checks"]
