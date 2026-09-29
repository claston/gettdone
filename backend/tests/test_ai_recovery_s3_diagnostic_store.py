from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.adapters.ai_recovery.s3_diagnostic_store import S3AIRecoveryResultStore
from app.application.ai_recovery.privacy_fixture import PrivacySafeFixture
from app.application.ai_recovery.request_publishing import build_ai_recovery_request_artifacts
from app.application.ai_recovery.schemas import AIRecoveryVersionSet


class _S3:
    def __init__(self) -> None:
        self.puts = []

    def put_object(self, **kwargs):
        self.puts.append(kwargs)


def _manifest():
    now = datetime(2026, 9, 28, tzinfo=UTC)
    return build_ai_recovery_request_artifacts(
        bucket="private-ai-recovery",
        analysis_id="an_store123",
        pdf_bytes=b"%PDF original",
        page_count=1,
        deterministic_artifact={"transactions": []},
        source_evidence={"pages": []},
        parser_release="release-123",
        layout_profile="nubank_statement_ptbr",
        layout_family="nubank_conta_digital",
        statement_type="conta_digital_extrato",
        layout_confidence=0.98,
        issue_codes=("balance_consistency_failed",),
        ai=AIRecoveryVersionSet(
            model_id="us.amazon.nova-2-lite-v1:0",
            prompt_version="nova_transaction_diagnosis_v1",
            output_schema_version="nova_diagnostic_v1",
            comparator_version="statement_comparator_v1",
        ),
        created_at=now,
        expires_at=now + timedelta(days=1),
    ).manifest


def test_fixture_manifest_is_written_last_and_every_result_is_immutable() -> None:
    client = _S3()
    store = S3AIRecoveryResultStore(bucket="private-ai-recovery", s3_client=client)
    fixture = PrivacySafeFixture(
        pdf_bytes=b"%PDF safe",
        manifest={"schema_version": "3", "privacy_validated": True},
        expected={"schema_version": "ai_recovery_fixture_expected_v1"},
        readme="# Safe fixture\n",
    )

    store.store_fixture(manifest=_manifest(), fixture=fixture)

    assert [call["Key"].rsplit("/", 1)[-1] for call in client.puts] == [
        "input.pdf",
        "expected.json",
        "README.md",
        "manifest.json",
    ]
    assert all(call["IfNoneMatch"] == "*" for call in client.puts)
    assert all(call["ServerSideEncryption"] == "AES256" for call in client.puts)
    assert all(call["Metadata"]["classification"] == "shareable-fixture" for call in client.puts)
