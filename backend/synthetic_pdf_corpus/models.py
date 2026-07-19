from __future__ import annotations

from dataclasses import dataclass
from typing import Any

SUPPORTED_VARIANTS = frozenset({"native_text", "scanned"})


@dataclass(frozen=True, slots=True)
class ExpectedTransaction:
    date: str
    description_contains: str
    amount: float
    transaction_type: str


@dataclass(frozen=True, slots=True)
class ScenarioExpectations:
    transactions: tuple[ExpectedTransaction, ...]
    max_false_positives: int = 0
    selected_parser: str = ""
    enforce_success: bool = True
    known_gap: str = ""


@dataclass(frozen=True, slots=True)
class SyntheticPdfScenario:
    schema_version: int
    scenario_id: str
    description: str
    pages: tuple[tuple[str, ...], ...]
    variants: tuple[str, ...]
    expected: ScenarioExpectations


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    expected_count: int
    actual_count: int
    matched_count: int
    false_positive_count: int
    recall: float
    precision: float
    success: bool
    missing_transactions: tuple[ExpectedTransaction, ...]
    unexpected_transactions: tuple[Any, ...]

    @property
    def summary(self) -> str:
        return (
            f"expected={self.expected_count} actual={self.actual_count} matched={self.matched_count} "
            f"false_positives={self.false_positive_count} recall={self.recall:.3f} "
            f"precision={self.precision:.3f}"
        )
