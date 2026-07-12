from pathlib import Path

from app.application.access_control import IdentityContext
from app.application.conversion.conversion_document_store import ConversionDocumentReference
from app.application.conversion.conversion_job import ConversionExecutionHooks, ConversionJob
from app.application.conversion.conversion_pipeline_result import ConversionPipelineResult
from app.application.conversion.convert_document_result import ConvertDocumentStatus
from app.application.conversion.convert_document_use_case import ConvertDocumentUseCase
from app.application.conversion.uploaded_document import UploadedDocument, UploadedDocumentStage
from app.schemas import (
    AnalyzeResponse,
    BeforeAfterPreview,
    CategorySummary,
    ConvertResponse,
    Insight,
    OperationalSummary,
    ReconciliationSummary,
    TopExpense,
    TransactionPreview,
)


class FakeConversionJobExecutor:
    def __init__(self) -> None:
        self.jobs: list[ConversionJob] = []
        self.hooks: list[ConversionExecutionHooks] = []

    def execute(
        self,
        *,
        job: ConversionJob,
        hooks: ConversionExecutionHooks,
    ) -> ConversionPipelineResult:
        self.jobs.append(job)
        self.hooks.append(hooks)
        analysis = AnalyzeResponse(
            analysis_id="an_use_case_001",
            file_type="pdf",
            transactions_total=1,
            total_inflows=100.0,
            total_outflows=-20.0,
            net_total=80.0,
            operational_summary=OperationalSummary(
                total_volume=120.0,
                inflow_count=1,
                outflow_count=1,
                reconciled_entries=0,
                unmatched_entries=1,
            ),
            reconciliation=ReconciliationSummary(
                matched_groups=0,
                reversed_entries=0,
                potential_duplicates=0,
            ),
            categories=[CategorySummary(category="Outros", total=-20.0, count=1)],
            top_expenses=[
                TopExpense(
                    description="TEST",
                    amount=-20.0,
                    date="2026-04-01",
                    category="Outros",
                )
            ],
            insights=[Insight(type="test", title="Test insight", description="Bytes: 9")],
            preview_transactions=[
                TransactionPreview(
                    date="2026-04-01",
                    description="TEST",
                    amount=-20.0,
                    category="Outros",
                    reconciliation_status="unmatched",
                )
            ],
            preview_before_after=[
                BeforeAfterPreview(
                    date="2026-04-01",
                    description_before="test",
                    description_after="TEST",
                    amount_before=-20.0,
                    amount_after=-20.0,
                )
            ],
            expires_at=None,
        )
        return ConversionPipelineResult.completed(
            payload=ConvertResponse(
                processing_id="an_use_case_001",
                quota_remaining=2,
                quota_limit=3,
                quota_mode="conversion",
                identity_type="anonymous",
                analysis=analysis,
            ).model_dump(),
            metadata={"page_count": 1, "ocr_used": False},
        )


class FakeConversionJobFactory:
    def create(self, **kwargs) -> ConversionJob:
        document = kwargs["document"]
        return ConversionJob.create(
            document=ConversionDocumentReference.from_document(document, storage_key="doc_1234567890abcdef12345678"),
            identity=IdentityContext(identity_type="anonymous", identity_id="anon_123", quota_limit=3),
            scanned_likely=kwargs.get("scanned_likely"),
            estimated_pages_count=kwargs.get("estimated_pages_count"),
        )


def test_convert_document_use_case_returns_application_result() -> None:
    executor = FakeConversionJobExecutor()
    use_case = ConvertDocumentUseCase(
        conversion_job_factory=FakeConversionJobFactory(),
        conversion_job_executor=executor,
    )
    staged_path = Path(__file__).parent / "fixtures" / "document_conversion_pipeline_statement.csv"

    result = use_case.execute(
        document=UploadedDocument.from_staged_upload(
            filename="statement.csv",
            staged_upload=UploadedDocumentStage(
                path=staged_path,
                size_bytes=staged_path.stat().st_size,
                sha256_hex="abc123",
            ),
        ),
        anonymous_fingerprint="anon-fp",
        user_token=None,
        authorization=None,
        access_cookie_token=None,
        scanned_likely=False,
        estimated_pages_count=None,
    )

    assert result.analysis_id == "an_use_case_001"
    assert result.status == ConvertDocumentStatus.COMPLETED
    assert result.payload is not None
    assert result.payload["processing_id"] == "an_use_case_001"
    assert result.payload["identity_type"] == "anonymous"
    assert executor.jobs[0].document.filename == "statement.csv"
    assert executor.jobs[0].identity.identity_id == "anon_123"
    assert executor.hooks[0].on_ocr_progress is None


def test_convert_document_use_case_maps_rejected_pipeline_result() -> None:
    class RejectedConversionJobExecutor:
        def execute(self, *, job: ConversionJob, hooks: ConversionExecutionHooks) -> ConversionPipelineResult:
            _ = job, hooks
            return ConversionPipelineResult.rejected(
                reason="quota_unavailable",
                message="Quota not available.",
                metadata={"required_quota": 1},
            )

    use_case = ConvertDocumentUseCase(
        conversion_job_factory=FakeConversionJobFactory(),
        conversion_job_executor=RejectedConversionJobExecutor(),
    )
    staged_path = Path(__file__).parent / "fixtures" / "document_conversion_pipeline_statement.csv"

    result = use_case.execute(
        document=UploadedDocument.from_staged_upload(
            filename="statement.csv",
            staged_upload=UploadedDocumentStage(
                path=staged_path,
                size_bytes=staged_path.stat().st_size,
                sha256_hex="abc123",
            ),
        ),
        anonymous_fingerprint="anon-fp",
        user_token=None,
        authorization=None,
        access_cookie_token=None,
        scanned_likely=False,
        estimated_pages_count=None,
    )

    assert result.status == ConvertDocumentStatus.REJECTED
    assert result.rejection_reason == "quota_unavailable"
    assert result.message == "Quota not available."
