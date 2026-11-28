"""Private file integrity, queue routing and independent retention cleanup."""

import hashlib
import json
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.adapters.conversion import pro_fiscal_runtime as runtime
from app.workers.conversion_lambda import handle_sqs_event, lambda_handler


@pytest.mark.parametrize("raw,expected", [
    ("true", True), ("1", True), (" YES ", True), ("on", True),
    ("false", False), ("0", False), (" NO ", False), ("off", False),
    ("invalid", None), ("", None), (None, None),
])
@pytest.mark.parametrize("default", [True, False])
def test_fiscal_flags_keep_api_parsing_contract(monkeypatch, raw, expected, default):
    from app.security_baseline import read_bool_env

    monkeypatch.delenv("PRO_FISCAL_ENABLED", raising=False)
    if raw is not None:
        monkeypatch.setenv("PRO_FISCAL_ENABLED", raw)
    assert runtime.read_bool_env("PRO_FISCAL_ENABLED", default=default) is (default if expected is None else expected)
    assert runtime.read_bool_env is read_bool_env


def test_fiscal_storage_checks_content_not_only_declared_digest():
    raw = b"<NFSe/>"
    client = SimpleNamespace(get_object=lambda **_: {"Body": BytesIO(raw)})
    uploads = SimpleNamespace(
        prefix="conversion/jobs/pro-fiscal", bucket="private", _client=lambda: client, _safe_filename=lambda value: value
    )
    objects = runtime.FiscalObjects(uploads)
    ref = objects.reference("nota.xml", len(raw), hashlib.sha256(raw).hexdigest())
    assert objects.read(ref) == raw
    with pytest.raises(ValueError, match="conteúdo"):
        objects.read({**ref, "sha256_hex": "0" * 64})
    with pytest.raises(ValueError):
        objects.reference("nota.exe", len(raw), ref["sha256_hex"])
    with pytest.raises(ValueError):
        objects.reference("nota.xml", 5 * 1024 * 1024 + 1, ref["sha256_hex"])


def test_fiscal_sqs_routes_separately_and_retries_only_failed_records(monkeypatch):
    calls = []

    def process(body):
        calls.append(body["import_id"])
        if body["import_id"] == "retry":
            raise ConnectionError("temporary")

    monkeypatch.setattr(runtime, "process_fiscal_message", process)
    records = [
        {"messageId": value, "body": json.dumps({"schema_version": 1, "event_type": "pro.fiscal.requested", "import_id": value})}
        for value in (str(uuid4()), "retry")
    ]
    result = handle_sqs_event({"Records": records}, processor=None, request_id="test-fiscal")
    assert len(calls) == 2
    assert result == {"batchItemFailures": [{"itemIdentifier": "retry"}]}


def test_rollback_keeps_cleanup_active_without_dispatch(monkeypatch):
    from app.workers import canonical_cleanup
    monkeypatch.setattr(canonical_cleanup, 'worker_handler', lambda: {'expired_pro_documents_removed': 3, 'expired_analyses_removed': 1})
    monkeypatch.setenv("PRO_FISCAL_ENABLED", "false")
    monkeypatch.setattr(
        runtime,
        "build_pro_fiscal_service",
        lambda: SimpleNamespace(cleanup=lambda: 2, dispatch=lambda: pytest.fail("disabled imports must not dispatch")),
    )
    assert lambda_handler({"action": "fiscal_maintenance"}, None) == {
        "fiscal_sources_removed": 2, "fiscal_messages_published": 0, 'expired_pro_documents_removed': 3, 'expired_analyses_removed': 1}
    with pytest.raises(RuntimeError, match="disabled"):
        runtime.process_fiscal_message({"import_id": str(uuid4())})
