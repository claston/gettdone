from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

from app.application.ai_recovery.config import AIRecoveryConfig
from app.application.ai_recovery.shadow_dispatch import AIRecoveryShadowDispatcher
from app.application.conversion.uploaded_document import UploadedDocument
from app.application.models import AnalysisData, TransactionRow


class _Publisher:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.artifacts = None

    def publish(self, artifacts):
        self.calls.append("s3")
        self.artifacts = artifacts
        return SimpleNamespace(ready_key=artifacts.manifest.document.key.replace("input.pdf", "ready.json"))


class _Queue:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.message = None

    def publish(self, message):
        self.calls.append("sqs")
        self.message = message
        return "message-1"


def _analysis() -> AnalysisData:
    row = TransactionRow(
        date="2026-09-01",
        description="PIX RECEBIDO FAR ENGENHARIA LTDA CPF 123.456.789-00",
        amount=125.50,
        category="Outros",
        reconciliation_status="unmatched",
        running_balance=1125.50,
        warning_types=["balance_consistency_failed"],
    )
    return AnalysisData(
        analysis_id="an_shadow123",
        file_type="pdf",
        upload_filename="statement.pdf",
        transactions_total=1,
        total_inflows=125.50,
        total_outflows=0.0,
        net_total=125.50,
        preview_transactions=[row],
        report_transactions=[row],
        layout_inference_name="nubank_statement_ptbr",
        layout_inference_confidence=0.98,
        bank_name="Nubank",
        bank_code="260",
        opening_balance=1000.0,
        closing_balance=1125.5,
        quality_issues=[
            {
                "scope": "transaction",
                "severity": "error",
                "issue_code": "balance_consistency_failed",
                "transaction_index": 0,
                "source_page": 1,
                "source_line": 8,
                "source_parser": "grouped",
            }
        ],
        pdf_processing_metrics={
            "page_count": 1,
            "selected_parser": "grouped",
            "canonical_warning_types_list": ["balance_consistency_failed"],
            "balance_consistency_failed": 1,
        },
    )


def _config(mode: str, *, bedrock_enabled: bool = False) -> AIRecoveryConfig:
    return AIRecoveryConfig.from_mapping(
        {
            "AI_RECOVERY_MODE": mode,
            "AI_RECOVERY_BEDROCK_ENABLED": str(bedrock_enabled).lower(),
        }
    )


def test_shadow_dispatch_publishes_original_pdf_before_queue_message() -> None:
    calls: list[str] = []
    publisher = _Publisher(calls)
    queue = _Queue(calls)
    dispatcher = AIRecoveryShadowDispatcher(
        config=_config("shadow", bedrock_enabled=True),
        bucket="private-ai-recovery",
        request_publisher=publisher,
        queue_publisher=queue,
        parser_release="release-123",
        clock=lambda: datetime(2026, 9, 28, 12, 0, tzinfo=UTC),
    )
    original = b"%PDF-1.7 original-private-document"

    result = dispatcher.dispatch(
        document=UploadedDocument(
            filename="statement.pdf",
            raw_bytes=original,
            file_type="pdf",
        ),
        analysis=_analysis(),
        page_texts=(
            "NUBANK\nFAR ENGENHARIA LTDA\n01/09/2026 PIX RECEBIDO FAR ENGENHARIA LTDA 125,50 1.125,50",
        ),
        source_layout_lines=None,
    )

    assert result.status == "queued"
    assert calls == ["s3", "sqs"]
    assert publisher.artifacts.pdf_bytes == original
    assert publisher.artifacts.manifest.deterministic_artifact.layout_profile == "nubank_statement_ptbr"
    assert publisher.artifacts.manifest.deterministic_artifact.statement_type == "conta_digital_extrato"
    assert queue.message.ready_key.endswith("/ready.json")
    assert queue.message.idempotency_key == publisher.artifacts.manifest.idempotency_key


def test_shadow_dispatch_stores_request_without_queueing_when_bedrock_is_disabled() -> None:
    calls: list[str] = []
    publisher = _Publisher(calls)
    queue = _Queue(calls)
    dispatcher = AIRecoveryShadowDispatcher(
        config=_config("shadow"),
        bucket="private-ai-recovery",
        request_publisher=publisher,
        queue_publisher=queue,
        parser_release="release-123",
        request_ttl_seconds=7 * 86_400,
        clock=lambda: datetime(2026, 9, 28, 12, 0, tzinfo=UTC),
    )

    result = dispatcher.dispatch(
        document=UploadedDocument(
            filename="statement.pdf",
            raw_bytes=b"%PDF-1.7 original-private-document",
            file_type="pdf",
        ),
        analysis=_analysis(),
        page_texts=("NUBANK\n01/09/2026 PIX RECEBIDO 125,50",),
        source_layout_lines=None,
    )

    assert result.status == "stored"
    assert result.reason == "bedrock_disabled"
    assert calls == ["s3"]
    assert result.ready_key == publisher.artifacts.manifest.document.key.replace("input.pdf", "ready.json")
    assert result.message_id is None
    assert publisher.artifacts.manifest.expires_at == datetime(2026, 9, 29, 12, 0, tzinfo=UTC)


def test_shadow_dispatch_stores_successful_generic_conversion() -> None:
    calls: list[str] = []
    publisher = _Publisher(calls)
    dispatcher = AIRecoveryShadowDispatcher(
        config=_config("active"),
        bucket="private-ai-recovery",
        request_publisher=publisher,
        queue_publisher=None,
        parser_release="release-123",
    )
    analysis = replace(
        _analysis(),
        layout_inference_name="generic_statement_ptbr",
        layout_inference_confidence=0.25,
        quality_issues=[],
        pdf_processing_metrics={
            "page_count": 1,
            "selected_parser": "generic_pdf",
            "canonical_warning_types_list": [],
            "balance_consistency_failed": 0,
        },
    )

    result = dispatcher.dispatch(
        document=UploadedDocument(
            filename="generic.pdf",
            raw_bytes=b"%PDF generic statement",
            file_type="pdf",
        ),
        analysis=analysis,
        page_texts=("01/09/2026 PIX RECEBIDO 125,50",),
        source_layout_lines=None,
    )

    assert result.status == "stored"
    assert calls == ["s3"]
    reference = publisher.artifacts.manifest.deterministic_artifact
    assert reference.layout_profile == "generic_statement_ptbr"
    assert reference.issue_codes == ["generic_layout"]


def test_shadow_dispatch_stores_allowlisted_conversion_failure() -> None:
    calls: list[str] = []
    publisher = _Publisher(calls)
    dispatcher = AIRecoveryShadowDispatcher(
        config=_config("active"),
        bucket="private-ai-recovery",
        request_publisher=publisher,
        queue_publisher=None,
        parser_release="release-123",
    )

    result = dispatcher.dispatch_failure(
        document=UploadedDocument(
            filename="unsupported.pdf",
            raw_bytes=b"%PDF unsupported statement",
            file_type="pdf",
        ),
        analysis_id="an_failure123",
        page_count=1,
        error_stage="parse",
        error_subcode="no_transaction_row_pattern",
        exception_class="InvalidFileContentError",
        parse_observability={},
    )

    assert result.status == "stored"
    assert calls == ["s3"]
    assert publisher.artifacts.manifest.analysis_id == "an_failure123"
    reference = publisher.artifacts.manifest.deterministic_artifact
    assert reference.layout_profile == "unknown"
    assert reference.issue_codes == ["no_transaction_row_pattern"]
    deterministic = json.loads(publisher.artifacts.deterministic_artifact)
    assert deterministic["transactions"] == []
    assert deterministic["failure"] == {
        "error_stage": "parse",
        "error_subcode": "no_transaction_row_pattern",
        "exception_class": "InvalidFileContentError",
    }


def test_shadow_dispatch_stores_rejected_document_for_manual_review_without_queueing() -> None:
    calls: list[str] = []
    publisher = _Publisher(calls)
    queue = _Queue(calls)
    dispatcher = AIRecoveryShadowDispatcher(
        config=_config("active", bedrock_enabled=True),
        bucket="private-ai-recovery",
        request_publisher=publisher,
        queue_publisher=queue,
        parser_release="release-123",
        clock=lambda: datetime(2026, 10, 10, 12, 0, tzinfo=UTC),
    )

    result = dispatcher.dispatch_failure(
        document=UploadedDocument(
            filename="invoice.pdf",
            raw_bytes=b"%PDF fiscal invoice for private manual review",
            file_type="pdf",
        ),
        analysis_id="an_rejected123",
        page_count=1,
        error_stage="parse",
        error_subcode="unsupported_document_type",
        exception_class="UnsupportedDocumentContentError",
        parse_observability={},
        document_classification=SimpleNamespace(
            document_type="fiscal_invoice",
            confidence=0.93,
            evidence=["nfe_danfe", "raw customer text must not persist", "nfe_access_key"],
            classifier_version="2026-10-10.1",
        ),
        document_processing_decision="rejected",
    )

    assert result.status == "stored"
    assert result.reason == "manual_classification_review"
    assert calls == ["s3"]
    assert queue.message is None
    reference = publisher.artifacts.manifest.deterministic_artifact
    assert reference.document_type == "fiscal_invoice"
    assert reference.document_type_confidence == 0.93
    assert reference.document_classification_version == "2026-10-10.1"
    assert reference.document_processing_decision == "rejected"
    assert reference.document_classification_evidence == ["nfe_danfe", "nfe_access_key"]
    deterministic = json.loads(publisher.artifacts.deterministic_artifact)
    assert deterministic["document_classification"] == {
        "document_type": "fiscal_invoice",
        "confidence": 0.93,
        "evidence": ["nfe_danfe", "nfe_access_key"],
        "classifier_version": "2026-10-10.1",
        "processing_decision": "rejected",
    }
    assert deterministic["failure"]["error_subcode"] == "unsupported_document_type"


def test_shadow_dispatch_is_disabled_by_default() -> None:
    calls: list[str] = []
    dispatcher = AIRecoveryShadowDispatcher(
        config=_config("off"),
        bucket="private-ai-recovery",
        request_publisher=_Publisher(calls),
        queue_publisher=_Queue(calls),
        parser_release="release-123",
    )

    result = dispatcher.dispatch(
        document=UploadedDocument(
            filename="statement.pdf",
            raw_bytes=b"%PDF original",
            file_type="pdf",
        ),
        analysis=_analysis(),
        page_texts=("text",),
        source_layout_lines=None,
    )

    assert result.status == "skipped"
    assert result.reason == "disabled"
    assert calls == []
