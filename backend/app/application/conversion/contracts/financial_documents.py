"""Additive, versioned contract for financial and commercial events."""
from typing import Any, Literal

from pydantic import BaseModel, Field


class FinancialSourceReference(BaseModel):
    page: int = Field(ge=1)
    line: int = Field(ge=1)


class FinancialEvent(BaseModel):
    event_id: str
    event_type: str
    status: str
    reference: str | None
    counterparty: str | None
    dates: dict[str, str]
    amounts: dict[str, str]
    amount: str
    amount_basis: str
    currency: str
    cash_effect: str | None
    description: str
    source_refs: list[FinancialSourceReference]
    attributes: dict[str, Any]


class FinancialObservation(BaseModel):
    observation_type: str
    dates: dict[str, str]
    amounts: dict[str, str]
    attributes: dict[str, Any]
    source_refs: list[FinancialSourceReference]


class FinancialValidation(BaseModel):
    code: str
    status: Literal["passed", "failed"]
    actual: str
    expected: str
    source_refs: list[FinancialSourceReference]


class FinancialExportPolicy(BaseModel):
    available_formats: list[Literal["csv", "xlsx", "ofx"]]
    ofx_profile: Literal["receipts"] | None
    requires_destination_account: bool
    supports_row_edits: bool
    reason: str


class FinancialDocumentPayload(BaseModel):
    schema_version: Literal["financial_events_v1"]
    document_type: str
    evidence: list[str]
    origin: str | None
    representation: str
    extraction_method: str
    metadata: dict[str, Any]
    events: list[FinancialEvent]
    observations: list[FinancialObservation]
    validations: list[FinancialValidation]
    notice: str
    export: FinancialExportPolicy
    source_document_sha256: str
    classification: dict[str, Any]
