from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.application.ai_recovery.diagnostic_processing import AIRecoveryDiagnosticProcessor
from app.application.ai_recovery.request_publishing import build_ai_recovery_request_artifacts
from app.application.ai_recovery.schemas import AIRecoveryVersionSet, NovaDiagnosticV1


def _diagnostic() -> NovaDiagnosticV1:
    return NovaDiagnosticV1.model_validate(
        {
            "schema_version": "nova_diagnostic_v1",
            "statement": {
                "schema_version": "nova_statement_v2",
                "document": {
                    "pages_examined": 1,
                    "all_pages_examined": True,
                    "transcription_truncated": False,
                    "visible_period": {"start_text": "01/09/2026", "end_text": "30/09/2026"},
                },
                "opening_balance": None,
                "closing_balance": None,
                "transactions": [
                    {
                        "visual_order": 1,
                        "page": 1,
                        "visual_line_start": 8,
                        "visual_line_end": 8,
                        "date_text": "01/09/2026",
                        "description_lines": ["PIX RECEBIDO"],
                        "amount_text": "125,50",
                        "direction": "credit",
                        "running_balance_text": "1.125,50",
                        "ambiguities": [],
                    }
                ],
                "unresolved_rows": [],
                "pages": [
                    {
                        "page": 1,
                        "transactions_observed": 1,
                        "repeated_header_observed": False,
                        "unresolved_rows_observed": 0,
                    }
                ],
                "warnings": [],
            },
            "conclusion": "parser_defect",
            "findings": [
                {
                    "code": "running_balance_used_as_amount",
                    "confidence": "0.96",
                    "explanation": "Saldo associado ao lançamento.",
                    "likely_parser_cause": "Colunas deslocadas.",
                    "suggested_fix": "Ancorar valor e saldo separadamente.",
                    "deterministic_indexes": [0],
                    "nova_visual_orders": [1],
                    "evidence": [{"page": 1, "visual_line_start": 8, "visual_line_end": 8}],
                }
            ],
        }
    )


class _Objects:
    def __init__(self, values: dict[tuple[str, str], bytes]) -> None:
        self.values = values

    def get_bytes(self, *, bucket: str, key: str) -> bytes:
        return self.values[(bucket, key)]


class _Analyzer:
    def __init__(self) -> None:
        self.calls = []

    def analyze(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            diagnostic=_diagnostic(),
            model_id="us.amazon.nova-2-lite-v1:0",
            prompt_version="nova_transaction_diagnosis_v1",
            provider_request_id="request-1",
            usage=SimpleNamespace(input_tokens=1000, output_tokens=400),
            latency_ms=123.4,
        )


class _FixtureBuilder:
    def __init__(self) -> None:
        self.calls = []

    def build(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            pdf_bytes=b"%PDF privacy-safe fixture",
            manifest={"schema_version": "3", "privacy_validated": True},
            expected={"schema_version": "ai_recovery_fixture_expected_v1"},
            readme="# Reproducao segura\n",
        )


class _Results:
    def __init__(self) -> None:
        self.restricted = []
        self.fixtures = []

    def store_restricted(self, **kwargs):
        self.restricted.append(kwargs)

    def store_fixture(self, **kwargs):
        self.fixtures.append(kwargs)


def _request_objects() -> tuple[object, dict[tuple[str, str], bytes]]:
    now = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
    deterministic = {
        "schema_version": "deterministic_statement_v1",
        "selected_parser": "grouped",
        "opening_balance": "1000.00",
        "closing_balance": "1125.50",
        "transactions": [
            {
                "index": 0,
                "date": "2026-09-01",
                "description": "PIX RECEBIDO FAR ENGENHARIA LTDA",
                "amount": "1125.50",
                "running_balance": "1125.50",
                "warning_types": ["balance_consistency_failed"],
                "source_issues": [{"source_page": 1, "source_line": 8}],
            }
        ],
    }
    source = {
        "schema_version": "source_evidence_v1",
        "classification": "restricted",
        "pages": [{"page": 1, "text": "FAR ENGENHARIA LTDA", "lines": []}],
    }
    artifacts = build_ai_recovery_request_artifacts(
        bucket="private-ai-recovery",
        analysis_id="an_shadow123",
        pdf_bytes=b"%PDF-1.7 original-private-document",
        page_count=1,
        deterministic_artifact=deterministic,
        source_evidence=source,
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
    )
    ready_key = artifacts.manifest.document.key.replace("input.pdf", "ready.json")
    values = {
        ("private-ai-recovery", ready_key): json.dumps(
            artifacts.manifest.model_dump(mode="json"), default=str
        ).encode(),
        ("private-ai-recovery", artifacts.manifest.document.key): artifacts.pdf_bytes,
        ("private-ai-recovery", artifacts.manifest.deterministic_artifact.key): artifacts.deterministic_artifact,
        ("private-ai-recovery", artifacts.manifest.deterministic_artifact.source_evidence_key): artifacts.source_evidence,
    }
    return (artifacts, values)


def test_processor_analyzes_original_then_stores_restricted_diagnostic_and_safe_fixture() -> None:
    artifacts, values = _request_objects()
    analyzer = _Analyzer()
    fixtures = _FixtureBuilder()
    results = _Results()
    processor = AIRecoveryDiagnosticProcessor(
        object_reader=_Objects(values),
        analyzer=analyzer,
        result_store=results,
        fixture_builder=fixtures,
        clock=lambda: datetime(2026, 9, 28, 13, 0, tzinfo=UTC),
    )
    ready_key = artifacts.manifest.document.key.replace("input.pdf", "ready.json")

    outcome = processor.process(
        bucket="private-ai-recovery",
        ready_key=ready_key,
        expected_idempotency_key=artifacts.manifest.idempotency_key,
    )

    assert outcome.status == "completed"
    assert analyzer.calls[0]["raw_bytes"] == artifacts.pdf_bytes
    assert hashlib.sha256(analyzer.calls[0]["raw_bytes"]).hexdigest() == artifacts.manifest.document.sha256
    assert results.restricted[0]["diagnostic"]["nova"]["findings"][0]["code"] == "running_balance_used_as_amount"
    assert fixtures.calls[0]["original_pdf"] == artifacts.pdf_bytes
    assert results.fixtures[0]["fixture"].manifest["privacy_validated"] is True


def test_processor_rejects_a_pdf_that_does_not_match_manifest_before_bedrock() -> None:
    artifacts, values = _request_objects()
    values[("private-ai-recovery", artifacts.manifest.document.key)] = b"%PDF-1.7 tampered-private-document"
    analyzer = _Analyzer()
    processor = AIRecoveryDiagnosticProcessor(
        object_reader=_Objects(values),
        analyzer=analyzer,
        result_store=_Results(),
        fixture_builder=_FixtureBuilder(),
        clock=lambda: datetime(2026, 9, 28, 13, 0, tzinfo=UTC),
    )

    try:
        processor.process(
            bucket="private-ai-recovery",
            ready_key=artifacts.manifest.document.key.replace("input.pdf", "ready.json"),
            expected_idempotency_key=artifacts.manifest.idempotency_key,
        )
    except ValueError as exc:
        assert "digest" in str(exc)
    else:
        raise AssertionError("tampered input should fail closed")
    assert analyzer.calls == []
