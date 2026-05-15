from dataclasses import dataclass
from typing import Any, Callable

from app.application.errors import InvalidFileContentError

RowsParser = Callable[[list[Any]], tuple[list[Any], int]]
TabularRowsParser = Callable[[list[Any], Any | None], tuple[list[Any], int]]


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


def select_parsed_rows(
    *,
    lines: list[Any],
    grouped_rows: list[Any],
    layout_profile: Any | None,
    parse_inline_rows: RowsParser,
    parse_tabular_rows: TabularRowsParser,
    parse_columnar_rows: RowsParser,
) -> SelectedParserRows:
    if grouped_rows:
        return SelectedParserRows(
            selected_parser="grouped",
            rows=grouped_rows,
            inline_candidates=0,
            inline_transactions_count=0,
            tabular_candidates=0,
            columnar_candidates=0,
            tabular_transactions_count=0,
            columnar_transactions_count=0,
            selection_reason="grouped_rows_available",
            inline_decision="skipped_due_to_grouped",
            tabular_decision="skipped_due_to_grouped",
            columnar_decision="skipped_due_to_grouped",
        )

    inline_rows, inline_candidates = parse_inline_rows(lines)
    inline_transactions_count = len(inline_rows)
    tabular_rows, tabular_candidates = parse_tabular_rows(lines, layout_profile)
    tabular_transactions_count = len(tabular_rows)
    columnar_rows, columnar_candidates = parse_columnar_rows(lines)
    columnar_transactions_count = len(columnar_rows)
    tabular_is_clearly_better_than_inline = tabular_transactions_count >= inline_transactions_count + 3

    if inline_rows and tabular_rows and tabular_is_clearly_better_than_inline:
        return SelectedParserRows(
            selected_parser="tabular",
            rows=tabular_rows,
            inline_candidates=inline_candidates,
            inline_transactions_count=inline_transactions_count,
            tabular_candidates=tabular_candidates,
            columnar_candidates=columnar_candidates,
            tabular_transactions_count=tabular_transactions_count,
            columnar_transactions_count=columnar_transactions_count,
            selection_reason="tabular_preferred_on_row_count_gap",
            inline_decision="not_selected_row_count_gap",
            tabular_decision="selected_on_row_count_gap",
            columnar_decision="no_rows" if not columnar_rows else "not_selected_tabular_priority",
        )

    if inline_rows and not (
        layout_profile is not None and tabular_transactions_count >= inline_transactions_count and tabular_rows
    ):
        return SelectedParserRows(
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
        )

    if inline_rows and layout_profile is not None and tabular_transactions_count >= inline_transactions_count and tabular_rows:
        return SelectedParserRows(
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
        )

    if tabular_rows:
        return SelectedParserRows(
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
        )

    if columnar_rows:
        return SelectedParserRows(
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
        )

    if inline_candidates > 0 or tabular_candidates > 0 or columnar_candidates > 0:
        raise InvalidFileContentError("PDF text was extracted, but transactions are in an unsupported table layout.")
    raise InvalidFileContentError("PDF text was extracted, but no recognizable transaction row pattern was found.")
