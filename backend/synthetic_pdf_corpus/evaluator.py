from __future__ import annotations

import unicodedata
from typing import Any, Iterable

from synthetic_pdf_corpus.models import EvaluationReport, ExpectedTransaction


def evaluate_transactions(
    expected: tuple[ExpectedTransaction, ...],
    actual: Iterable[Any],
    *,
    max_false_positives: int,
) -> EvaluationReport:
    actual_transactions = tuple(actual)
    available_indexes = set(range(len(actual_transactions)))
    missing: list[ExpectedTransaction] = []
    matched_count = 0

    for expected_transaction in expected:
        matching_index = next(
            (
                index
                for index in sorted(available_indexes)
                if _matches(expected_transaction, actual_transactions[index])
            ),
            None,
        )
        if matching_index is None:
            missing.append(expected_transaction)
            continue
        available_indexes.remove(matching_index)
        matched_count += 1

    unexpected = tuple(actual_transactions[index] for index in sorted(available_indexes))
    expected_count = len(expected)
    actual_count = len(actual_transactions)
    recall = matched_count / expected_count if expected_count else 1.0
    precision = matched_count / actual_count if actual_count else (1.0 if not expected_count else 0.0)
    return EvaluationReport(
        expected_count=expected_count,
        actual_count=actual_count,
        matched_count=matched_count,
        false_positive_count=len(unexpected),
        recall=round(recall, 6),
        precision=round(precision, 6),
        success=matched_count == expected_count and len(unexpected) <= max_false_positives,
        missing_transactions=tuple(missing),
        unexpected_transactions=unexpected,
    )


def _matches(expected: ExpectedTransaction, actual: Any) -> bool:
    if str(getattr(actual, "date", "")) != expected.date:
        return False
    if abs(float(getattr(actual, "amount", 0.0)) - expected.amount) > 0.02:
        return False
    if str(getattr(actual, "type", "")) != expected.transaction_type:
        return False
    expected_description = _normalize_text(expected.description_contains)
    actual_description = _normalize_text(str(getattr(actual, "description", "")))
    return expected_description in actual_description


def _normalize_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    without_accents = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(without_accents.upper().split())
