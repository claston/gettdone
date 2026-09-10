import json
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _imported_modules(module: str) -> set[str]:
    result = subprocess.run(
        [sys.executable, "-c", f"import importlib, json, sys; importlib.import_module({module!r}); print(json.dumps(sorted(sys.modules)))"],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return set(json.loads(result.stdout))


@pytest.mark.parametrize("contract", ["access", "documents", "jobs", "batches", "uploads"])
def test_conversion_contracts_do_not_import_runtime_implementations(contract: str) -> None:
    loaded = _imported_modules(f"app.application.conversion.contracts.{contract}")
    forbidden = (
        "app.adapters",
        "app.api",
        "app.schemas",
        "app.workers",
        "app.dependencies",
        "app.application.parsers",
        "app.application.pdf_parser",
        "app.application.quota_management",
        "boto3",
        "psycopg",
        "fastapi",
    )
    assert not {name for name in loaded if any(name == prefix or name.startswith(prefix + ".") for prefix in forbidden)}


def test_worker_does_not_import_http_schema_collection() -> None:
    loaded = _imported_modules("app.workers.conversion_lambda")
    assert "app.schemas" not in loaded


def test_shared_payload_keeps_the_http_conversion_schema() -> None:
    from app.application.conversion.contracts.payloads import ConvertResponse as ConversionPayload
    from app.schemas import ConvertResponse

    assert ConvertResponse is ConversionPayload
    baseline = json.loads((BACKEND_ROOT / "tests/fixtures/contracts/conversion_payload_schema.json").read_text(encoding="utf-8"))
    assert ConversionPayload.model_json_schema() == baseline
    assert ConvertResponse.model_json_schema()["required"] == [
        "processing_id",
        "quota_remaining",
        "quota_limit",
        "identity_type",
        "analysis",
    ]
