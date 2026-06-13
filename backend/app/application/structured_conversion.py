from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.application.models import AnalysisData, BeforeAfterRow, TransactionRow

STRUCTURED_CONVERSION_CONTRACT_VERSION = "2026-06-13"


@dataclass
class StructuredSourceFile:
    filename: str | None
    file_type: str


@dataclass
class StructuredDocumentInfo:
    semantic_type: str | None = None
    semantic_confidence: float | None = None
    semantic_evidence: list[str] = field(default_factory=list)
    bank_name: str | None = None
    layout_inference_name: str | None = None
    layout_inference_confidence: float | None = None


@dataclass
class StructuredAccountInfo:
    bank_code: str | None = None
    bank_branch: str | None = None
    account_number: str | None = None
    account_type: str | None = None


@dataclass
class StructuredBalanceInfo:
    opening_balance: float | None = None
    closing_balance: float | None = None


@dataclass
class StructuredConversionTransaction:
    transaction_id: str
    date: str
    description: str
    amount: float
    transaction_type: str
    category: str
    reconciliation_status: str
    running_balance: float | None = None
    warning_types: list[str] = field(default_factory=list)
    is_deleted: bool = False


@dataclass
class StructuredBeforeAfterRecord:
    date: str
    description_before: str
    description_after: str
    amount_before: float
    amount_after: float


@dataclass
class StructuredResultSummary:
    transactions_total: int
    total_inflows: float
    total_outflows: float
    net_total: float
    matched_groups: int
    reversed_entries: int
    potential_duplicates: int


@dataclass
class StructuredExportInfo:
    recommended_review: bool
    recommendation_code: str | None = None
    recommendation_reason: str | None = None
    available_formats: list[str] = field(default_factory=lambda: ["json", "ofx", "csv", "xlsx"])


@dataclass
class StructuredResultTimestamps:
    updated_at: str | None = None
    expires_at: str | None = None


@dataclass
class StructuredConversionResult:
    conversion_id: str
    analysis_id: str
    source: StructuredSourceFile
    document: StructuredDocumentInfo
    account: StructuredAccountInfo
    balances: StructuredBalanceInfo
    summary: StructuredResultSummary
    transactions: list[StructuredConversionTransaction]
    before_after: list[StructuredBeforeAfterRecord]
    export: StructuredExportInfo
    timestamps: StructuredResultTimestamps
    contract_version: str = STRUCTURED_CONVERSION_CONTRACT_VERSION
    metrics: dict[str, Any] | None = None


def build_structured_conversion_result_from_analysis_data(
    analysis_data: AnalysisData,
    *,
    expires_at: str | None = None,
) -> StructuredConversionResult:
    report_rows = analysis_data.report_transactions or analysis_data.preview_transactions
    metrics = dict(analysis_data.pdf_processing_metrics or {}) or None
    recommendation_code = None if metrics is None else str(metrics.get("export_recommendation", "") or "").strip() or None
    recommendation_reason = (
        None
        if metrics is None
        else str(metrics.get("export_recommendation_reason", "") or "").strip() or None
    )

    return StructuredConversionResult(
        conversion_id=analysis_data.analysis_id,
        analysis_id=analysis_data.analysis_id,
        source=StructuredSourceFile(
            filename=analysis_data.upload_filename,
            file_type=analysis_data.file_type,
        ),
        document=StructuredDocumentInfo(
            semantic_type=analysis_data.semantic_type,
            semantic_confidence=analysis_data.semantic_confidence,
            semantic_evidence=list(analysis_data.semantic_evidence or []),
            bank_name=analysis_data.bank_name,
            layout_inference_name=analysis_data.layout_inference_name,
            layout_inference_confidence=analysis_data.layout_inference_confidence,
        ),
        account=StructuredAccountInfo(
            bank_code=analysis_data.bank_code,
            bank_branch=analysis_data.bank_branch,
            account_number=analysis_data.account_number,
            account_type=analysis_data.ofx_account_type,
        ),
        balances=StructuredBalanceInfo(
            opening_balance=analysis_data.opening_balance,
            closing_balance=analysis_data.closing_balance,
        ),
        summary=StructuredResultSummary(
            transactions_total=analysis_data.transactions_total,
            total_inflows=analysis_data.total_inflows,
            total_outflows=analysis_data.total_outflows,
            net_total=analysis_data.net_total,
            matched_groups=analysis_data.matched_groups,
            reversed_entries=analysis_data.reversed_entries,
            potential_duplicates=analysis_data.potential_duplicates,
        ),
        transactions=[
            _build_structured_transaction(row=row, index=index)
            for index, row in enumerate(report_rows, start=1)
        ],
        before_after=[
            _build_structured_before_after_record(item)
            for item in analysis_data.preview_before_after
        ],
        export=StructuredExportInfo(
            recommended_review=recommendation_code == "review_recommended",
            recommendation_code=recommendation_code,
            recommendation_reason=recommendation_reason,
        ),
        timestamps=StructuredResultTimestamps(
            updated_at=analysis_data.updated_at,
            expires_at=expires_at,
        ),
        metrics=metrics,
    )


def _build_structured_transaction(
    *,
    row: TransactionRow,
    index: int,
) -> StructuredConversionTransaction:
    transaction_type = "inflow" if float(row.amount) >= 0 else "outflow"
    return StructuredConversionTransaction(
        transaction_id=f"txn_{index:04d}",
        date=row.date,
        description=row.description,
        amount=row.amount,
        transaction_type=transaction_type,
        category=row.category,
        reconciliation_status=row.reconciliation_status,
        running_balance=row.running_balance,
        warning_types=list(row.warning_types or []),
        is_deleted=bool(row.is_deleted),
    )


def _build_structured_before_after_record(item: BeforeAfterRow) -> StructuredBeforeAfterRecord:
    return StructuredBeforeAfterRecord(
        date=item.date,
        description_before=item.description_before,
        description_after=item.description_after,
        amount_before=item.amount_before,
        amount_after=item.amount_after,
    )
