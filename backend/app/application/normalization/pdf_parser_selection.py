import math
import re
from dataclasses import dataclass, replace
from typing import Any, Callable

from app.application.errors import InvalidFileContentError
from app.application.normalization.pdf_amount_tokens import contains_amount_like
from app.application.normalization.pdf_row_match_rules import contains_date_like
from app.application.normalization.pdf_tabular_profile_rules import (
    find_profile_tabular_amount_tokens,
    profile_date_formats,
)
from app.application.normalization.text import normalize_upper_text

RowsParser = Callable[[list[Any]], tuple[list[Any], int]]
TabularRowsParser = Callable[[list[Any], Any | None], tuple[list[Any], int]]
MultilineRowsParser = Callable[[list[Any], Any | None], tuple[list[Any], int]]

@dataclass(frozen=True)
class SelectedParserRows:
    selected_parser: str
    rows: list[Any]
    inline_candidates: int
    inline_transactions_count: int
    tabular_candidates: int
    columnar_candidates: int
    tabular_transactions_count: int
    columnar_transactions_count: int
    selection_reason: str
    inline_decision: str
    tabular_decision: str
    columnar_decision: str
    multiline_candidates: int = 0
    multiline_transactions_count: int = 0
    multiline_decision: str = "not_evaluated"
    multiline_overlap_count: int = 0
    multiline_coverage_gain: int = 0
    multiline_conflict_count: int = 0


@dataclass(frozen=True)
class _MultilineCoverageAssessment:
    overlap_count: int
    coverage_gain: int
    conflict_count: int
    should_select: bool
    decision: str


def _row_transaction(row: Any) -> Any | None:
    transaction = getattr(row, "transaction", None)
    if transaction is None:
        return None
    if not str(getattr(transaction, "date", "")).strip():
        return None
    if getattr(transaction, "amount", None) is None:
        return None
    return transaction


def _normalized_description_tokens(row: Any) -> set[str]:
    transaction = _row_transaction(row)
    if transaction is None:
        return set()
    normalized = normalize_upper_text(str(getattr(transaction, "description", "")))
    return {token for token in re.findall(r"[A-Z0-9]+", normalized) if len(token) >= 2}


def _descriptions_are_compatible(left: Any, right: Any) -> bool:
    left_tokens = _normalized_description_tokens(left)
    right_tokens = _normalized_description_tokens(right)
    if not left_tokens or not right_tokens:
        return False
    shared_count = len(left_tokens & right_tokens)
    return shared_count / min(len(left_tokens), len(right_tokens)) >= 0.6


def _same_source_position(left: Any, right: Any) -> bool:
    left_page = getattr(left, "source_page", None)
    right_page = getattr(right, "source_page", None)
    left_line = getattr(left, "source_line", None)
    right_line = getattr(right, "source_line", None)
    return (
        left_page is not None
        and right_page is not None
        and left_line is not None
        and right_line is not None
        and left_page == right_page
        and left_line == right_line
    )


def _rows_share_identity(left: Any, right: Any) -> bool:
    left_transaction = _row_transaction(left)
    right_transaction = _row_transaction(right)
    if left_transaction is None or right_transaction is None:
        return False
    if getattr(left_transaction, "date") != getattr(right_transaction, "date"):
        return False
    return _same_source_position(left, right) or _descriptions_are_compatible(left, right)


def _rows_match(left: Any, right: Any) -> bool:
    if not _rows_share_identity(left, right):
        return False
    left_amount = float(getattr(_row_transaction(left), "amount"))
    right_amount = float(getattr(_row_transaction(right), "amount"))
    return abs(left_amount - right_amount) <= 0.02


def _rows_conflict(left: Any, right: Any) -> bool:
    if not _rows_share_identity(left, right):
        return False
    left_amount = float(getattr(_row_transaction(left), "amount"))
    right_amount = float(getattr(_row_transaction(right), "amount"))
    return abs(left_amount - right_amount) > 0.02


def _assess_multiline_coverage(
    *,
    primary_rows: list[Any],
    multiline_rows: list[Any],
) -> _MultilineCoverageAssessment:
    coverage_gain = max(0, len(multiline_rows) - len(primary_rows))
    conflict_count = sum(
        1
        for primary_row in primary_rows
        if any(_rows_conflict(primary_row, multiline_row) for multiline_row in multiline_rows)
    )

    available_indexes = set(range(len(multiline_rows)))
    overlap_count = 0
    for primary_row in primary_rows:
        matching_index = next(
            (
                index
                for index in sorted(available_indexes)
                if _rows_match(primary_row, multiline_rows[index])
            ),
            None,
        )
        if matching_index is None:
            continue
        available_indexes.remove(matching_index)
        overlap_count += 1

    required_gain = max(2, math.ceil(len(primary_rows) * 0.5))
    if conflict_count:
        decision = "not_selected_conflicting_primary_rows"
    elif coverage_gain == 0:
        decision = "not_selected_no_coverage_gain"
    elif overlap_count < len(primary_rows):
        decision = "not_selected_primary_rows_not_covered"
    elif coverage_gain < required_gain:
        decision = "not_selected_insufficient_coverage_gain"
    else:
        decision = "selected_on_clear_coverage_gain"

    return _MultilineCoverageAssessment(
        overlap_count=overlap_count,
        coverage_gain=coverage_gain,
        conflict_count=conflict_count,
        should_select=decision == "selected_on_clear_coverage_gain",
        decision=decision,
    )


def _apply_multiline_coverage_selection(
    selection: SelectedParserRows,
    *,
    multiline_rows: list[Any],
    multiline_candidates: int,
) -> SelectedParserRows:
    if not multiline_rows:
        return replace(
            selection,
            multiline_candidates=multiline_candidates,
            multiline_decision="no_rows",
        )

    assessment = _assess_multiline_coverage(
        primary_rows=selection.rows,
        multiline_rows=multiline_rows,
    )
    metrics = {
        "multiline_candidates": multiline_candidates,
        "multiline_transactions_count": len(multiline_rows),
        "multiline_decision": assessment.decision,
        "multiline_overlap_count": assessment.overlap_count,
        "multiline_coverage_gain": assessment.coverage_gain,
        "multiline_conflict_count": assessment.conflict_count,
    }
    if not assessment.should_select:
        return replace(selection, **metrics)

    decision_updates: dict[str, str] = {}
    primary_decision_field = {
        "inline": "inline_decision",
        "tabular": "tabular_decision",
        "columnar": "columnar_decision",
    }.get(selection.selected_parser)
    if primary_decision_field is not None:
        decision_updates[primary_decision_field] = "not_selected_multiline_coverage_gain"
    return replace(
        selection,
        selected_parser="multiline",
        rows=multiline_rows,
        selection_reason=f"multiline_preferred_over_{selection.selected_parser}_on_coverage_gain",
        **metrics,
        **decision_updates,
    )


def _resolve_line_texts(lines: list[Any]) -> list[str]:
    resolved: list[str] = []
    for item in lines:
        if isinstance(item, str):
            text = item.strip()
        else:
            text = str(getattr(item, "text", "")).strip()
        if text:
            resolved.append(text)
    return resolved


def _build_pdf_pattern_failure_detail(
    *,
    base_message: str,
    lines: list[Any],
    inline_candidates: int,
    tabular_candidates: int,
    columnar_candidates: int,
    multiline_candidates: int,
    layout_profile: Any | None,
) -> str:
    line_texts = _resolve_line_texts(lines)
    joined = "\n".join(line_texts)
    has_date_like = contains_date_like(joined, date_formats=profile_date_formats(layout_profile))
    has_amount_like = contains_amount_like(joined) or bool(
        find_profile_tabular_amount_tokens(joined, layout_profile)
    )
    missing_signals: list[str] = []
    if not has_date_like:
        missing_signals.append("date_pattern")
    if not has_amount_like:
        missing_signals.append("amount_pattern")
    if (
        inline_candidates == 0
        and tabular_candidates == 0
        and columnar_candidates == 0
        and multiline_candidates == 0
    ):
        missing_signals.append("transaction_row_pattern")
    detail_suffix = (
        " diagnostics:"
        f" has_date_like={int(has_date_like)}"
        f" has_amount_like={int(has_amount_like)}"
        f" inline_candidates={inline_candidates}"
        f" tabular_candidates={tabular_candidates}"
        f" columnar_candidates={columnar_candidates}"
        f" multiline_candidates={multiline_candidates}"
        f" missing_signals={','.join(sorted(set(missing_signals)))}"
    )
    return base_message + detail_suffix


def _should_prefer_tabular_over_grouped_on_traceability(
    *, grouped_rows: list[Any], tabular_rows: list[Any], grouped_transactions_count: int, tabular_transactions_count: int
) -> bool:
    if grouped_transactions_count == 0 or tabular_transactions_count == 0:
        return False
    if grouped_transactions_count != tabular_transactions_count:
        return False

    grouped_source_lines = [getattr(item, "source_line", None) for item in grouped_rows]
    tabular_source_lines = [getattr(item, "source_line", None) for item in tabular_rows]
    grouped_descriptions = [str(getattr(getattr(item, "transaction", None), "description", "")).strip().upper() for item in grouped_rows]
    tabular_descriptions = [str(getattr(getattr(item, "transaction", None), "description", "")).strip().upper() for item in tabular_rows]
    if any(line is None for line in grouped_source_lines) or any(line is None for line in tabular_source_lines):
        return False
    if not any(
        (" DEBITO" in tabular_description or " CREDITO" in tabular_description)
        and (" DEBITO" not in grouped_description and " CREDITO" not in grouped_description)
        for grouped_description, tabular_description in zip(grouped_descriptions, tabular_descriptions)
    ):
        return False
    return all(int(grouped_line) > int(tabular_line) for grouped_line, tabular_line in zip(grouped_source_lines, tabular_source_lines))


def _should_prefer_tabular_over_grouped_on_credit_debit_columns(
    *, grouped_rows: list[Any], tabular_rows: list[Any], layout_profile: Any | None
) -> bool:
    if layout_profile is None:
        return False
    expected_column_order = tuple(getattr(layout_profile, "expected_column_order", ()) or ())
    if "credit" not in expected_column_order and "debit" not in expected_column_order:
        return False
    if not grouped_rows or len(grouped_rows) != len(tabular_rows):
        return False

    for grouped_row, tabular_row in zip(grouped_rows, tabular_rows, strict=False):
        grouped_tx = getattr(grouped_row, "transaction", None)
        tabular_tx = getattr(tabular_row, "transaction", None)
        if grouped_tx is None or tabular_tx is None:
            return False
        if getattr(grouped_tx, "date", None) != getattr(tabular_tx, "date", None):
            return False
        if getattr(grouped_tx, "description", None) != getattr(tabular_tx, "description", None):
            return False

    return any(
        getattr(getattr(grouped_row, "transaction", None), "amount", 0.0)
        != getattr(getattr(tabular_row, "transaction", None), "amount", 0.0)
        for grouped_row, tabular_row in zip(grouped_rows, tabular_rows, strict=False)
    )


def _should_prefer_tabular_over_grouped_on_opening_balance_noise(
    *, grouped_rows: list[Any], tabular_rows: list[Any], layout_profile: Any | None
) -> bool:
    if layout_profile is None or not grouped_rows or not tabular_rows:
        return False
    if len(grouped_rows) != len(tabular_rows) + 1:
        return False

    grouped_descriptions = [
        str(getattr(getattr(item, "transaction", None), "description", "")).strip().upper() for item in grouped_rows
    ]
    tabular_descriptions = [
        str(getattr(getattr(item, "transaction", None), "description", "")).strip().upper() for item in tabular_rows
    ]

    grouped_has_opening_balance = any(
        description.startswith("SALDO ANTERIOR") or description.startswith("SALDO INICIAL")
        for description in grouped_descriptions
    )
    tabular_has_opening_balance = any(
        description.startswith("SALDO ANTERIOR") or description.startswith("SALDO INICIAL")
        for description in tabular_descriptions
    )
    grouped_has_repeated_header_noise = any(
        "DATA MOV." in description and "NR. DOC." in description and "VALOR" in description and "SALDO" in description
        for description in grouped_descriptions
    )
    tabular_has_repeated_header_noise = any(
        "DATA MOV." in description and "NR. DOC." in description and "VALOR" in description and "SALDO" in description
        for description in tabular_descriptions
    )

    return (
        grouped_has_opening_balance
        and not tabular_has_opening_balance
        and (
            grouped_has_repeated_header_noise
            or all(
                grouped_description == tabular_description
                for grouped_description, tabular_description in zip(
                    grouped_descriptions[1:],
                    tabular_descriptions,
                    strict=False,
                )
            )
        )
        and not tabular_has_repeated_header_noise
    )


def _should_prefer_tabular_over_inline_on_opening_balance_noise(
    *, inline_rows: list[Any], tabular_rows: list[Any], layout_profile: Any | None
) -> bool:
    if layout_profile is None or not inline_rows or not tabular_rows:
        return False
    if len(inline_rows) != len(tabular_rows) + 1:
        return False

    inline_descriptions = [
        str(getattr(item, "description", getattr(getattr(item, "transaction", None), "description", ""))).strip().upper()
        for item in inline_rows
    ]
    tabular_descriptions = [
        str(getattr(getattr(item, "transaction", None), "description", "")).strip().upper() for item in tabular_rows
    ]

    inline_has_opening_balance = any(
        description.startswith("SALDO ANTERIOR") or description.startswith("SALDO INICIAL")
        for description in inline_descriptions
    )
    tabular_has_opening_balance = any(
        description.startswith("SALDO ANTERIOR") or description.startswith("SALDO INICIAL")
        for description in tabular_descriptions
    )

    return (
        inline_has_opening_balance
        and not tabular_has_opening_balance
        and all(
            inline_description == tabular_description
            for inline_description, tabular_description in zip(
                inline_descriptions[1:],
                tabular_descriptions,
                strict=False,
            )
        )
    )


def select_parsed_rows(
    *,
    lines: list[Any],
    grouped_rows: list[Any],
    layout_profile: Any | None,
    parse_inline_rows: RowsParser,
    parse_tabular_rows: TabularRowsParser,
    parse_columnar_rows: RowsParser,
    parse_multiline_rows: MultilineRowsParser | None = None,
) -> SelectedParserRows:
    multiline_rows: list[Any] = []
    multiline_candidates = 0
    multiline_error: InvalidFileContentError | None = None
    if parse_multiline_rows is not None:
        try:
            multiline_rows, multiline_candidates = parse_multiline_rows(lines, layout_profile)
        except InvalidFileContentError as exc:
            multiline_error = exc

    def finalize_primary_selection(selection: SelectedParserRows) -> SelectedParserRows:
        if parse_multiline_rows is None:
            return selection
        if multiline_error is not None:
            return replace(selection, multiline_decision="evaluation_failed_primary_preserved")
        return _apply_multiline_coverage_selection(
            selection,
            multiline_rows=multiline_rows,
            multiline_candidates=multiline_candidates,
        )

    if grouped_rows:
        inline_rows, inline_candidates = parse_inline_rows(lines)
        inline_transactions_count = len(inline_rows)
        tabular_rows, tabular_candidates = parse_tabular_rows(lines, layout_profile)
        tabular_transactions_count = len(tabular_rows)
        columnar_rows, columnar_candidates = parse_columnar_rows(lines)
        columnar_transactions_count = len(columnar_rows)
        grouped_transactions_count = len(grouped_rows)
        tabular_is_clearly_better_than_grouped = tabular_transactions_count >= grouped_transactions_count + 3
        tabular_preserves_credit_debit_signals = _should_prefer_tabular_over_grouped_on_credit_debit_columns(
            grouped_rows=grouped_rows,
            tabular_rows=tabular_rows,
            layout_profile=layout_profile,
        )
        tabular_avoids_opening_balance_noise = _should_prefer_tabular_over_grouped_on_opening_balance_noise(
            grouped_rows=grouped_rows,
            tabular_rows=tabular_rows,
            layout_profile=layout_profile,
        )
        tabular_preserves_better_traceability_than_grouped = _should_prefer_tabular_over_grouped_on_traceability(
            grouped_rows=grouped_rows,
            tabular_rows=tabular_rows,
            grouped_transactions_count=grouped_transactions_count,
            tabular_transactions_count=tabular_transactions_count,
        )

        if tabular_rows and (
            tabular_is_clearly_better_than_grouped
            or tabular_preserves_credit_debit_signals
            or tabular_avoids_opening_balance_noise
            or tabular_preserves_better_traceability_than_grouped
        ):
            return finalize_primary_selection(SelectedParserRows(
                selected_parser="tabular",
                rows=tabular_rows,
                inline_candidates=inline_candidates,
                inline_transactions_count=inline_transactions_count,
                tabular_candidates=tabular_candidates,
                columnar_candidates=columnar_candidates,
                tabular_transactions_count=tabular_transactions_count,
                columnar_transactions_count=columnar_transactions_count,
                selection_reason=(
                    "tabular_preferred_over_grouped_on_row_count_gap"
                    if tabular_is_clearly_better_than_grouped
                    else "tabular_preferred_over_grouped_on_credit_debit_columns"
                    if tabular_preserves_credit_debit_signals
                    else "tabular_preferred_over_grouped_opening_balance_noise"
                    if tabular_avoids_opening_balance_noise
                    else "tabular_preferred_over_grouped_on_traceability"
                ),
                inline_decision="not_selected_grouped_overridden",
                tabular_decision=(
                    "selected_on_grouped_override_row_count_gap"
                    if tabular_is_clearly_better_than_grouped
                    else "selected_on_grouped_override_credit_debit_columns"
                    if tabular_preserves_credit_debit_signals
                    else "selected_on_grouped_override_opening_balance_noise"
                    if tabular_avoids_opening_balance_noise
                    else "selected_on_grouped_override_traceability"
                ),
                columnar_decision="no_rows" if not columnar_rows else "not_selected_tabular_priority",
            ))

        return finalize_primary_selection(SelectedParserRows(
            selected_parser="grouped",
            rows=grouped_rows,
            inline_candidates=inline_candidates,
            inline_transactions_count=inline_transactions_count,
            tabular_candidates=tabular_candidates,
            columnar_candidates=columnar_candidates,
            tabular_transactions_count=tabular_transactions_count,
            columnar_transactions_count=columnar_transactions_count,
            selection_reason="grouped_rows_available",
            inline_decision="not_selected_grouped_priority" if inline_rows else "no_rows",
            tabular_decision="not_selected_grouped_priority" if tabular_rows else "no_rows",
            columnar_decision="not_selected_grouped_priority" if columnar_rows else "no_rows",
        ))

    inline_rows, inline_candidates = parse_inline_rows(lines)
    inline_transactions_count = len(inline_rows)
    tabular_rows, tabular_candidates = parse_tabular_rows(lines, layout_profile)
    tabular_transactions_count = len(tabular_rows)
    columnar_rows, columnar_candidates = parse_columnar_rows(lines)
    columnar_transactions_count = len(columnar_rows)
    tabular_is_clearly_better_than_inline = tabular_transactions_count >= inline_transactions_count + 3
    tabular_avoids_inline_opening_balance_noise = _should_prefer_tabular_over_inline_on_opening_balance_noise(
        inline_rows=inline_rows,
        tabular_rows=tabular_rows,
        layout_profile=layout_profile,
    )

    if inline_rows and tabular_rows and (tabular_is_clearly_better_than_inline or tabular_avoids_inline_opening_balance_noise):
        return finalize_primary_selection(SelectedParserRows(
            selected_parser="tabular",
            rows=tabular_rows,
            inline_candidates=inline_candidates,
            inline_transactions_count=inline_transactions_count,
            tabular_candidates=tabular_candidates,
            columnar_candidates=columnar_candidates,
            tabular_transactions_count=tabular_transactions_count,
            columnar_transactions_count=columnar_transactions_count,
            selection_reason=(
                "tabular_preferred_on_row_count_gap"
                if tabular_is_clearly_better_than_inline
                else "tabular_preferred_over_inline_opening_balance_noise"
            ),
            inline_decision=(
                "not_selected_row_count_gap"
                if tabular_is_clearly_better_than_inline
                else "not_selected_opening_balance_noise"
            ),
            tabular_decision=(
                "selected_on_row_count_gap"
                if tabular_is_clearly_better_than_inline
                else "selected_on_inline_opening_balance_noise"
            ),
            columnar_decision="no_rows" if not columnar_rows else "not_selected_tabular_priority",
        ))

    if inline_rows and not (
        layout_profile is not None and tabular_transactions_count >= inline_transactions_count and tabular_rows
    ):
        return finalize_primary_selection(SelectedParserRows(
            selected_parser="inline",
            rows=inline_rows,
            inline_candidates=inline_candidates,
            inline_transactions_count=inline_transactions_count,
            tabular_candidates=tabular_candidates,
            columnar_candidates=columnar_candidates,
            tabular_transactions_count=tabular_transactions_count,
            columnar_transactions_count=columnar_transactions_count,
            selection_reason="inline_rows_available_after_grouped_empty",
            inline_decision="selected",
            tabular_decision="not_selected_inline_priority" if tabular_rows else "no_rows",
            columnar_decision="not_selected_inline_priority" if columnar_rows else "no_rows",
        ))

    if inline_rows and layout_profile is not None and tabular_transactions_count >= inline_transactions_count and tabular_rows:
        return finalize_primary_selection(SelectedParserRows(
            selected_parser="tabular",
            rows=tabular_rows,
            inline_candidates=inline_candidates,
            inline_transactions_count=inline_transactions_count,
            tabular_candidates=tabular_candidates,
            columnar_candidates=columnar_candidates,
            tabular_transactions_count=tabular_transactions_count,
            columnar_transactions_count=columnar_transactions_count,
            selection_reason="tabular_preferred_on_conflict_with_layout_profile",
            inline_decision="not_selected_conflict_lost_to_tabular",
            tabular_decision="selected_on_conflict",
            columnar_decision="no_rows" if not columnar_rows else "not_selected_tabular_priority",
        ))

    if tabular_rows:
        return finalize_primary_selection(SelectedParserRows(
            selected_parser="tabular",
            rows=tabular_rows,
            inline_candidates=inline_candidates,
            inline_transactions_count=inline_transactions_count,
            tabular_candidates=tabular_candidates,
            columnar_candidates=columnar_candidates,
            tabular_transactions_count=tabular_transactions_count,
            columnar_transactions_count=columnar_transactions_count,
            selection_reason="tabular_rows_available_after_inline_empty",
            inline_decision="no_rows",
            tabular_decision="selected",
            columnar_decision="no_rows" if not columnar_rows else "not_selected_tabular_priority",
        ))

    if columnar_rows:
        return finalize_primary_selection(SelectedParserRows(
            selected_parser="columnar",
            rows=columnar_rows,
            inline_candidates=inline_candidates,
            inline_transactions_count=inline_transactions_count,
            tabular_candidates=tabular_candidates,
            columnar_candidates=columnar_candidates,
            tabular_transactions_count=tabular_transactions_count,
            columnar_transactions_count=columnar_transactions_count,
            selection_reason="columnar_rows_available_after_tabular_empty",
            inline_decision="no_rows",
            tabular_decision="no_rows",
            columnar_decision="selected",
        ))

    if multiline_rows:
        return SelectedParserRows(
            selected_parser="multiline",
            rows=multiline_rows,
            inline_candidates=inline_candidates,
            inline_transactions_count=inline_transactions_count,
            tabular_candidates=tabular_candidates,
            columnar_candidates=columnar_candidates,
            tabular_transactions_count=tabular_transactions_count,
            columnar_transactions_count=columnar_transactions_count,
            selection_reason="multiline_rows_available_after_existing_parsers_empty",
            inline_decision="no_rows",
            tabular_decision="no_rows",
            columnar_decision="no_rows",
            multiline_candidates=multiline_candidates,
            multiline_transactions_count=len(multiline_rows),
            multiline_decision="selected_after_existing_parsers_empty",
        )

    if multiline_error is not None:
        raise multiline_error

    if inline_candidates > 0 or tabular_candidates > 0 or columnar_candidates > 0 or multiline_candidates > 0:
        raise InvalidFileContentError(
            _build_pdf_pattern_failure_detail(
                base_message="PDF text was extracted, but transactions are in an unsupported table layout.",
                lines=lines,
                inline_candidates=inline_candidates,
                tabular_candidates=tabular_candidates,
                columnar_candidates=columnar_candidates,
                multiline_candidates=multiline_candidates,
                layout_profile=layout_profile,
            )
        )
    raise InvalidFileContentError(
        _build_pdf_pattern_failure_detail(
            base_message="PDF text was extracted, but no recognizable transaction row pattern was found.",
            lines=lines,
            inline_candidates=inline_candidates,
            tabular_candidates=tabular_candidates,
            columnar_candidates=columnar_candidates,
            multiline_candidates=multiline_candidates,
            layout_profile=layout_profile,
        )
    )
