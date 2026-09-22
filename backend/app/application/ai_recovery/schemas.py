from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr, StringConstraints, model_validator

MAX_AI_RECOVERY_PDF_BYTES = 25 * 1024 * 1024

ObservedText = Annotated[StrictStr, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
ShortIdentifier = Annotated[StrictStr, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
ObjectKey = Annotated[StrictStr, StringConstraints(strip_whitespace=True, min_length=1, max_length=1024)]
Sha256Hex = Annotated[StrictStr, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
AnalysisId = Annotated[StrictStr, StringConstraints(pattern=r"^[A-Za-z][A-Za-z0-9_-]{1,127}$")]


class StrictContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class NovaTransactionDirection(str, Enum):
    CREDIT = "credit"
    DEBIT = "debit"
    UNKNOWN = "unknown"


class NovaAmbiguousField(str, Enum):
    DATE = "date"
    DESCRIPTION = "description"
    AMOUNT = "amount"
    DIRECTION = "direction"
    RUNNING_BALANCE = "running_balance"
    ROW_BOUNDARY = "row_boundary"


class NovaAmbiguityReason(str, Enum):
    ILLEGIBLE = "illegible"
    PARTIALLY_OCCLUDED = "partially_occluded"
    DIRECTION_NOT_EXPLICIT = "direction_not_explicit"
    DATE_SCOPE_UNCLEAR = "date_scope_unclear"
    ROW_BOUNDARY_UNCLEAR = "row_boundary_unclear"


class NovaUnresolvedRowReason(str, Enum):
    ILLEGIBLE = "illegible"
    TRANSACTION_BOUNDARY_UNCLEAR = "transaction_boundary_unclear"
    AMOUNT_COLUMN_UNCLEAR = "amount_column_unclear"
    DATE_ASSOCIATION_UNCLEAR = "date_association_unclear"
    POSSIBLE_TRANSACTION = "possible_transaction"


class NovaWarning(str, Enum):
    POSSIBLE_MISSING_PAGE = "possible_missing_page"
    MIXED_STATEMENT_SECTIONS = "mixed_statement_sections"
    VISUAL_ORDER_UNCERTAIN = "visual_order_uncertain"
    DOCUMENT_PARTIALLY_UNREADABLE = "document_partially_unreadable"


class NovaVisiblePeriod(StrictContractModel):
    start_text: ObservedText | None
    end_text: ObservedText | None


class NovaDocumentSummary(StrictContractModel):
    pages_examined: int = Field(ge=1, le=15)
    all_pages_examined: bool
    transcription_truncated: bool
    visible_period: NovaVisiblePeriod


class NovaBalanceEvidence(StrictContractModel):
    page: int = Field(ge=1, le=15)
    visual_line: int = Field(ge=1)
    amount_text: ObservedText
    direction: NovaTransactionDirection


class NovaFieldAmbiguity(StrictContractModel):
    field: NovaAmbiguousField
    reason: NovaAmbiguityReason


class NovaTransactionV2(StrictContractModel):
    visual_order: int = Field(ge=1)
    page: int = Field(ge=1, le=15)
    visual_line_start: int = Field(ge=1)
    visual_line_end: int = Field(ge=1)
    date_text: ObservedText | None
    description_lines: list[ObservedText] = Field(min_length=1)
    amount_text: ObservedText
    direction: NovaTransactionDirection
    running_balance_text: ObservedText | None
    ambiguities: list[NovaFieldAmbiguity]

    @model_validator(mode="after")
    def validate_line_range(self) -> NovaTransactionV2:
        if self.visual_line_end < self.visual_line_start:
            raise ValueError("visual_line_end must be greater than or equal to visual_line_start.")
        return self


class NovaUnresolvedRow(StrictContractModel):
    visual_order: int = Field(ge=1)
    page: int = Field(ge=1, le=15)
    visual_line_start: int = Field(ge=1)
    visual_line_end: int = Field(ge=1)
    reason: NovaUnresolvedRowReason
    visible_text_lines: list[ObservedText] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_line_range(self) -> NovaUnresolvedRow:
        if self.visual_line_end < self.visual_line_start:
            raise ValueError("visual_line_end must be greater than or equal to visual_line_start.")
        return self


class NovaPageSummary(StrictContractModel):
    page: int = Field(ge=1, le=15)
    transactions_observed: int = Field(ge=0)
    repeated_header_observed: bool
    unresolved_rows_observed: int = Field(ge=0)


class NovaStatementV2(StrictContractModel):
    schema_version: Literal["nova_statement_v2"]
    document: NovaDocumentSummary
    opening_balance: NovaBalanceEvidence | None
    closing_balance: NovaBalanceEvidence | None
    transactions: list[NovaTransactionV2]
    unresolved_rows: list[NovaUnresolvedRow]
    pages: list[NovaPageSummary] = Field(min_length=1, max_length=15)
    warnings: list[NovaWarning]

    @model_validator(mode="after")
    def validate_document_coverage(self) -> NovaStatementV2:
        expected_pages = list(range(1, self.document.pages_examined + 1))
        observed_pages = sorted(page.page for page in self.pages)
        if observed_pages != expected_pages:
            raise ValueError("pages must contain each examined page exactly once.")

        row_positions = [row.visual_order for row in self.transactions]
        row_positions.extend(row.visual_order for row in self.unresolved_rows)
        if row_positions and sorted(row_positions) != list(range(1, len(row_positions) + 1)):
            raise ValueError("visual_order must be unique and contiguous across transaction-like rows.")

        page_references = [row.page for row in self.transactions]
        page_references.extend(row.page for row in self.unresolved_rows)
        page_references.extend(
            balance.page for balance in (self.opening_balance, self.closing_balance) if balance is not None
        )
        if any(page > self.document.pages_examined for page in page_references):
            raise ValueError("row and balance page references must be within pages_examined.")

        transaction_counts = {
            page: sum(1 for row in self.transactions if row.page == page) for page in expected_pages
        }
        unresolved_counts = {
            page: sum(1 for row in self.unresolved_rows if row.page == page) for page in expected_pages
        }
        for summary in self.pages:
            if summary.transactions_observed != transaction_counts[summary.page]:
                raise ValueError("transactions_observed must match transactions for the page.")
            if summary.unresolved_rows_observed != unresolved_counts[summary.page]:
                raise ValueError("unresolved_rows_observed must match unresolved rows for the page.")
        return self


class AIRecoveryDocumentReference(StrictContractModel):
    bucket: ShortIdentifier
    key: ObjectKey
    sha256: Sha256Hex
    content_type: Literal["application/pdf"]
    size_bytes: int = Field(ge=1, le=MAX_AI_RECOVERY_PDF_BYTES)
    page_count: int = Field(ge=1, le=15)


class DeterministicArtifactReference(StrictContractModel):
    key: ObjectKey
    source_evidence_key: ObjectKey
    parser_release: ShortIdentifier
    layout_profile: ShortIdentifier
    layout_family: ShortIdentifier
    statement_type: ShortIdentifier
    layout_confidence: float = Field(ge=0.0, le=1.0)
    issue_codes: list[ShortIdentifier] = Field(min_length=1)


class AIRecoveryVersionSet(StrictContractModel):
    model_id: ShortIdentifier
    prompt_version: ShortIdentifier
    output_schema_version: Literal["nova_statement_v2"]
    comparator_version: ShortIdentifier


class AIRecoveryRequestManifest(StrictContractModel):
    schema_version: Literal["ai_recovery_request_v1"]
    idempotency_key: Sha256Hex
    analysis_id: AnalysisId
    document: AIRecoveryDocumentReference
    deterministic_artifact: DeterministicArtifactReference
    ai: AIRecoveryVersionSet
    created_at: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def validate_expiration(self) -> AIRecoveryRequestManifest:
        if self.created_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("created_at and expires_at must include timezone information.")
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at must be after created_at.")
        return self
