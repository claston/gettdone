from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from app.application.ai_recovery.contracts import DocumentAIExtractor
from app.application.ai_recovery.errors import AIExtractionError
from app.application.ai_recovery.financial_validator import FinancialValidator
from app.application.ai_recovery.models import AIStatement, FinancialValidationDisposition

_SIX_PLACES = Decimal("0.000001")


@dataclass(frozen=True, slots=True)
class AIRecoveryBenchmarkCase:
    case_id: str
    filename: str
    raw_bytes: bytes
    page_count: int
    expected: AIStatement


@dataclass(frozen=True, slots=True)
class AIRecoveryBenchmarkCaseResult:
    case_id: str
    completed: bool
    exact: bool
    financially_exact: bool
    expected_transactions: int
    actual_transactions: int
    correct_dates: int
    correct_amounts: int
    correct_directions: int
    correct_descriptions: int
    field_denominator: int
    validator_disposition: str | None
    critical_false_accept: bool
    input_tokens: int
    output_tokens: int
    latency_ms: float
    error_code: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "completed": self.completed,
            "exact": self.exact,
            "financially_exact": self.financially_exact,
            "expected_transactions": self.expected_transactions,
            "actual_transactions": self.actual_transactions,
            "validator_disposition": self.validator_disposition,
            "critical_false_accept": self.critical_false_accept,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "latency_ms": self.latency_ms,
            "error_code": self.error_code,
        }


@dataclass(frozen=True, slots=True)
class AIRecoveryBenchmarkReport:
    total_cases: int
    completed_cases: int
    extraction_failures: int
    exact_cases: int
    approved_cases: int
    rejected_cases: int
    inconclusive_cases: int
    critical_false_accepts: int
    date_accuracy: Decimal
    amount_accuracy: Decimal
    direction_accuracy: Decimal
    description_accuracy: Decimal
    input_tokens: int
    output_tokens: int
    latency_p50_ms: float
    latency_p95_ms: float
    cases: tuple[AIRecoveryBenchmarkCaseResult, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "total_cases": self.total_cases,
            "completed_cases": self.completed_cases,
            "extraction_failures": self.extraction_failures,
            "exact_cases": self.exact_cases,
            "approved_cases": self.approved_cases,
            "rejected_cases": self.rejected_cases,
            "inconclusive_cases": self.inconclusive_cases,
            "critical_false_accepts": self.critical_false_accepts,
            "date_accuracy": float(self.date_accuracy),
            "amount_accuracy": float(self.amount_accuracy),
            "direction_accuracy": float(self.direction_accuracy),
            "description_accuracy": float(self.description_accuracy),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "latency_p50_ms": self.latency_p50_ms,
            "latency_p95_ms": self.latency_p95_ms,
            "cases": [case.to_dict() for case in self.cases],
        }


def run_ai_recovery_benchmark(
    *,
    cases: list[AIRecoveryBenchmarkCase],
    extractor: DocumentAIExtractor,
    validator: FinancialValidator | None = None,
) -> AIRecoveryBenchmarkReport:
    financial_validator = validator or FinancialValidator()
    results: list[AIRecoveryBenchmarkCaseResult] = []

    for case in cases:
        try:
            extraction = extractor.extract(
                filename=case.filename,
                raw_bytes=case.raw_bytes,
                page_count=case.page_count,
            )
        except AIExtractionError as exc:
            results.append(_failed_case(case, error_code=exc.code.value))
            continue
        except Exception:
            results.append(_failed_case(case, error_code="unexpected_error"))
            continue

        validation = financial_validator.validate(extraction.statement, expected_page_count=case.page_count)
        comparison = _compare_statements(case.expected, extraction.statement)
        usage = extraction.usage
        critical_false_accept = validation.approved and not comparison["financially_exact"]
        results.append(
            AIRecoveryBenchmarkCaseResult(
                case_id=case.case_id,
                completed=True,
                exact=comparison["exact"],
                financially_exact=comparison["financially_exact"],
                expected_transactions=len(case.expected.transactions),
                actual_transactions=len(extraction.statement.transactions),
                correct_dates=comparison["correct_dates"],
                correct_amounts=comparison["correct_amounts"],
                correct_directions=comparison["correct_directions"],
                correct_descriptions=comparison["correct_descriptions"],
                field_denominator=comparison["field_denominator"],
                validator_disposition=validation.disposition.value,
                critical_false_accept=critical_false_accept,
                input_tokens=usage.input_tokens if usage is not None else 0,
                output_tokens=usage.output_tokens if usage is not None else 0,
                latency_ms=round(max(0.0, extraction.latency_ms), 3),
            )
        )

    return _summarize(results)


def _failed_case(case: AIRecoveryBenchmarkCase, *, error_code: str) -> AIRecoveryBenchmarkCaseResult:
    expected_count = len(case.expected.transactions)
    return AIRecoveryBenchmarkCaseResult(
        case_id=case.case_id,
        completed=False,
        exact=False,
        financially_exact=False,
        expected_transactions=expected_count,
        actual_transactions=0,
        correct_dates=0,
        correct_amounts=0,
        correct_directions=0,
        correct_descriptions=0,
        field_denominator=expected_count,
        validator_disposition=None,
        critical_false_accept=False,
        input_tokens=0,
        output_tokens=0,
        latency_ms=0.0,
        error_code=error_code,
    )


def _compare_statements(expected: AIStatement, actual: AIStatement) -> dict[str, int | bool]:
    denominator = max(len(expected.transactions), len(actual.transactions))
    correct_dates = 0
    correct_amounts = 0
    correct_directions = 0
    correct_descriptions = 0
    financially_exact = len(expected.transactions) == len(actual.transactions)
    exact = financially_exact

    for expected_transaction, actual_transaction in zip(expected.transactions, actual.transactions, strict=False):
        date_matches = expected_transaction.date == actual_transaction.date
        amount_matches = expected_transaction.amount == actual_transaction.amount
        direction_matches = expected_transaction.direction == actual_transaction.direction
        description_matches = _normalize_description(expected_transaction.description) == _normalize_description(
            actual_transaction.description
        )
        correct_dates += int(date_matches)
        correct_amounts += int(amount_matches)
        correct_directions += int(direction_matches)
        correct_descriptions += int(description_matches)
        financially_exact = financially_exact and all(
            (
                date_matches,
                amount_matches,
                direction_matches,
                expected_transaction.running_balance == actual_transaction.running_balance,
                expected_transaction.source_page == actual_transaction.source_page,
            )
        )
        exact = exact and all(
            (
                date_matches,
                amount_matches,
                direction_matches,
                description_matches,
                expected_transaction.running_balance == actual_transaction.running_balance,
                expected_transaction.source_page == actual_transaction.source_page,
                expected_transaction.source_line == actual_transaction.source_line,
            )
        )

    bounds_match = (
        expected.opening_balance == actual.opening_balance and expected.closing_balance == actual.closing_balance
    )
    financially_exact = financially_exact and bounds_match
    exact = exact and bounds_match and expected.period_start == actual.period_start and expected.period_end == actual.period_end
    return {
        "exact": exact,
        "financially_exact": financially_exact,
        "correct_dates": correct_dates,
        "correct_amounts": correct_amounts,
        "correct_directions": correct_directions,
        "correct_descriptions": correct_descriptions,
        "field_denominator": denominator,
    }


def _summarize(results: list[AIRecoveryBenchmarkCaseResult]) -> AIRecoveryBenchmarkReport:
    completed = [result for result in results if result.completed]
    denominator = sum(result.field_denominator for result in results)
    latencies = [result.latency_ms for result in completed]
    return AIRecoveryBenchmarkReport(
        total_cases=len(results),
        completed_cases=len(completed),
        extraction_failures=len(results) - len(completed),
        exact_cases=sum(result.exact for result in completed),
        approved_cases=sum(
            result.validator_disposition == FinancialValidationDisposition.APPROVED.value for result in completed
        ),
        rejected_cases=sum(
            result.validator_disposition == FinancialValidationDisposition.REJECTED.value for result in completed
        ),
        inconclusive_cases=sum(
            result.validator_disposition == FinancialValidationDisposition.INCONCLUSIVE.value for result in completed
        ),
        critical_false_accepts=sum(result.critical_false_accept for result in completed),
        date_accuracy=_accuracy(sum(result.correct_dates for result in results), denominator),
        amount_accuracy=_accuracy(sum(result.correct_amounts for result in results), denominator),
        direction_accuracy=_accuracy(sum(result.correct_directions for result in results), denominator),
        description_accuracy=_accuracy(sum(result.correct_descriptions for result in results), denominator),
        input_tokens=sum(result.input_tokens for result in completed),
        output_tokens=sum(result.output_tokens for result in completed),
        latency_p50_ms=round(_percentile(latencies, 0.50), 3),
        latency_p95_ms=round(_percentile(latencies, 0.95), 3),
        cases=tuple(results),
    )


def _accuracy(correct: int, total: int) -> Decimal:
    if total <= 0:
        return Decimal("0").quantize(_SIX_PLACES)
    return (Decimal(correct) / Decimal(total)).quantize(_SIX_PLACES)


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    ordered = sorted(values)
    rank = (len(ordered) - 1) * percentile
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    fraction = rank - low
    return ordered[low] * (1 - fraction) + ordered[high] * fraction


def _normalize_description(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().casefold())
