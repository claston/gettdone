from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from synthetic_pdf_corpus.evaluator import evaluate_transactions
from synthetic_pdf_corpus.models import EvaluationReport, SyntheticPdfScenario

PdfParser = Callable[[bytes], Any]


@dataclass(frozen=True, slots=True)
class CorpusRunResult:
    scenario_id: str
    variant: str
    evaluation: EvaluationReport | None
    selected_parser: str
    parser_selection_reason: str
    expected_parser: str
    parser_matches_expectation: bool
    error: str
    enforce_success: bool
    known_gap: str
    expected_transaction_count: int

    @property
    def success(self) -> bool:
        return (
            self.evaluation is not None
            and self.evaluation.success
            and self.parser_matches_expectation
            and not self.error
        )

    @property
    def blocks_ci(self) -> bool:
        return self.enforce_success and not self.success


@dataclass(frozen=True, slots=True)
class CorpusSummary:
    evaluated_count: int
    passed_count: int
    known_gap_count: int
    blocking_failure_count: int
    expected_transaction_count: int
    actual_transaction_count: int
    matched_transaction_count: int
    false_positive_count: int
    recall: float
    precision: float


def evaluate_pdf(
    scenario: SyntheticPdfScenario,
    *,
    variant: str,
    raw_pdf: bytes,
    parse_pdf: PdfParser,
) -> CorpusRunResult:
    try:
        parse_result = parse_pdf(raw_pdf)
    except Exception as exc:  # pragma: no cover - exercised by optional local OCR engines
        return CorpusRunResult(
            scenario_id=scenario.scenario_id,
            variant=variant,
            evaluation=None,
            selected_parser="error",
            parser_selection_reason="",
            expected_parser=scenario.expected.selected_parser,
            parser_matches_expectation=False,
            error=f"{type(exc).__name__}: {exc}",
            enforce_success=scenario.expected.enforce_success,
            known_gap=scenario.expected.known_gap,
            expected_transaction_count=len(scenario.expected.transactions),
        )

    evaluation = evaluate_transactions(
        scenario.expected.transactions,
        parse_result.transactions,
        max_false_positives=scenario.expected.max_false_positives,
    )
    metrics = parse_result.parse_metrics
    selected_parser = str(metrics.get("selected_parser", ""))
    parser_selection_reason = str(metrics.get("parser_selection_reason", ""))
    expected_parser = scenario.expected.selected_parser
    return CorpusRunResult(
        scenario_id=scenario.scenario_id,
        variant=variant,
        evaluation=evaluation,
        selected_parser=selected_parser,
        parser_selection_reason=parser_selection_reason,
        expected_parser=expected_parser,
        parser_matches_expectation=not expected_parser or selected_parser == expected_parser,
        error="",
        enforce_success=scenario.expected.enforce_success,
        known_gap=scenario.expected.known_gap,
        expected_transaction_count=len(scenario.expected.transactions),
    )


def summarize_results(results: list[CorpusRunResult]) -> CorpusSummary:
    expected_count = sum(result.expected_transaction_count for result in results)
    evaluations = [result.evaluation for result in results if result.evaluation is not None]
    actual_count = sum(evaluation.actual_count for evaluation in evaluations)
    matched_count = sum(evaluation.matched_count for evaluation in evaluations)
    false_positive_count = sum(evaluation.false_positive_count for evaluation in evaluations)
    recall = matched_count / expected_count if expected_count else 1.0
    precision = matched_count / actual_count if actual_count else (1.0 if not expected_count else 0.0)
    return CorpusSummary(
        evaluated_count=len(results),
        passed_count=sum(result.success for result in results),
        known_gap_count=sum(not result.success and not result.blocks_ci for result in results),
        blocking_failure_count=sum(result.blocks_ci for result in results),
        expected_transaction_count=expected_count,
        actual_transaction_count=actual_count,
        matched_transaction_count=matched_count,
        false_positive_count=false_positive_count,
        recall=round(recall, 6),
        precision=round(precision, 6),
    )
