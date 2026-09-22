from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.application.ai_recovery.schemas import AIRecoveryRequestManifest, NovaStatementV2


def _statement_payload() -> dict[str, object]:
    return {
        "schema_version": "nova_statement_v2",
        "document": {
            "pages_examined": 1,
            "all_pages_examined": True,
            "transcription_truncated": False,
            "visible_period": {"start_text": "01/08/2026", "end_text": "31/08/2026"},
        },
        "opening_balance": {
            "page": 1,
            "visual_line": 5,
            "amount_text": "1.000,00",
            "direction": "credit",
        },
        "closing_balance": {
            "page": 1,
            "visual_line": 12,
            "amount_text": "1.500,00",
            "direction": "credit",
        },
        "transactions": [
            {
                "visual_order": 1,
                "page": 1,
                "visual_line_start": 8,
                "visual_line_end": 9,
                "date_text": "02/08/2026",
                "description_lines": ["PIX recebido", "Cliente sintético"],
                "amount_text": "500,00",
                "direction": "credit",
                "running_balance_text": "1.500,00",
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
    }


def _request_payload() -> dict[str, object]:
    created_at = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
    return {
        "schema_version": "ai_recovery_request_v1",
        "idempotency_key": "b" * 64,
        "analysis_id": "an_9f94d351c367",
        "document": {
            "bucket": "gettdone-private",
            "key": "ai-recovery/requests/v1/key/input.pdf",
            "sha256": "a" * 64,
            "content_type": "application/pdf",
            "size_bytes": 123_456,
            "page_count": 1,
        },
        "deterministic_artifact": {
            "key": "ai-recovery/requests/v1/key/deterministic.json",
            "source_evidence_key": "ai-recovery/requests/v1/key/source-evidence.json",
            "parser_release": "a681d70",
            "layout_profile": "banco_inter_extrato_conta_corrente_saldo_transacao_v1",
            "layout_family": "banco_inter_conta_corrente_saldo_por_transacao",
            "statement_type": "conta_corrente_extrato",
            "layout_confidence": 0.98,
            "issue_codes": ["balance_consistency_failed"],
        },
        "ai": {
            "model_id": "us.amazon.nova-2-lite-v1:0",
            "prompt_version": "nova_bank_statement_v2",
            "output_schema_version": "nova_statement_v2",
            "comparator_version": "statement_comparator_v1",
        },
        "created_at": created_at.isoformat(),
        "expires_at": (created_at + timedelta(days=1)).isoformat(),
    }


def test_nova_statement_v2_preserves_observed_text_without_native_line_claims() -> None:
    statement = NovaStatementV2.model_validate(_statement_payload())

    assert statement.transactions[0].description_lines == ["PIX recebido", "Cliente sintético"]
    assert statement.transactions[0].amount_text == "500,00"
    assert statement.transactions[0].visual_line_end == 9


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload["transactions"][0].update({"amount_text": 500.0}),
        lambda payload: payload["transactions"][0].update({"native_line_ids": ["line-8"]}),
        lambda payload: payload["transactions"][0].update({"visual_order": 2}),
        lambda payload: payload["transactions"][0].update({"page": 2}),
        lambda payload: payload["transactions"][0].update({"visual_line_end": 7}),
    ],
)
def test_nova_statement_v2_fails_closed_for_untraceable_or_invalid_rows(mutate) -> None:
    payload = _statement_payload()
    mutate(payload)

    with pytest.raises(ValidationError):
        NovaStatementV2.model_validate(payload)


def test_ai_recovery_request_manifest_contains_only_refs_versions_and_safe_metadata() -> None:
    request = AIRecoveryRequestManifest.model_validate(_request_payload())

    assert request.document.sha256 == "a" * 64
    assert request.analysis_id == "an_9f94d351c367"
    assert request.deterministic_artifact.issue_codes == ["balance_consistency_failed"]
    assert request.ai.output_schema_version == "nova_statement_v2"
    assert "transactions" not in request.model_dump()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.update({"idempotency_key": "invalid"}),
        lambda payload: payload["document"].update({"sha256": "invalid"}),
        lambda payload: payload["document"].update({"size_bytes": (25 * 1024 * 1024) + 1}),
        lambda payload: payload.update({"expires_at": payload["created_at"]}),
        lambda payload: payload["deterministic_artifact"].update({"issue_codes": []}),
        lambda payload: payload.update({"raw_pdf": "not-allowed"}),
    ],
)
def test_ai_recovery_request_manifest_rejects_unsafe_or_unversioned_inputs(mutate) -> None:
    payload = _request_payload()
    mutate(payload)

    with pytest.raises(ValidationError):
        AIRecoveryRequestManifest.model_validate(payload)
