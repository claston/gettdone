from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

Money = Annotated[Decimal, Field(max_digits=18, decimal_places=2)]
NonNegativeMoney = Annotated[Decimal, Field(ge=Decimal("0"), max_digits=18, decimal_places=2)]
Description = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class TransactionDirection(str, Enum):
    CREDIT = "credit"
    DEBIT = "debit"
    UNKNOWN = "unknown"


class AITransaction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: date | None
    description: Description
    amount: NonNegativeMoney
    direction: TransactionDirection
    running_balance: Money | None = None
    source_page: int = Field(ge=1)
    source_line: int | None = Field(default=None, ge=1)

    @field_validator("amount", "running_balance", mode="before")
    @classmethod
    def reject_binary_money_values(cls, value: object) -> object:
        if value is None:
            return value
        if isinstance(value, bool) or isinstance(value, (int, float)):
            raise ValueError("Monetary values must be decimal strings.")
        if not isinstance(value, (str, Decimal)):
            raise ValueError("Monetary values must be decimal strings.")
        return value


class AIStatement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transactions: list[AITransaction]
    opening_balance: Money | None = None
    closing_balance: Money | None = None
    period_start: date | None = None
    period_end: date | None = None
    warnings: list[str] = Field(default_factory=list)

    @field_validator("opening_balance", "closing_balance", mode="before")
    @classmethod
    def reject_binary_money_values(cls, value: object) -> object:
        if value is None:
            return value
        if isinstance(value, bool) or isinstance(value, (int, float)):
            raise ValueError("Monetary values must be decimal strings.")
        if not isinstance(value, (str, Decimal)):
            raise ValueError("Monetary values must be decimal strings.")
        return value


class FinancialValidationDisposition(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True, slots=True)
class FinancialValidationIssue:
    code: str
    transaction_index: int | None = None


@dataclass(frozen=True, slots=True)
class FinancialValidationResult:
    disposition: FinancialValidationDisposition
    score: Decimal
    rule_version: str
    transactions_count: int
    balance_checks_count: int
    suspected_duplicates_count: int
    errors: tuple[FinancialValidationIssue, ...]
    warnings: tuple[FinancialValidationIssue, ...]

    @property
    def approved(self) -> bool:
        return self.disposition == FinancialValidationDisposition.APPROVED

    @property
    def error_codes(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(issue.code for issue in self.errors))

    @property
    def warning_codes(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(issue.code for issue in self.warnings))
