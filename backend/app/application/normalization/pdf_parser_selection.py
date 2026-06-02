from dataclasses import dataclass
from typing import Any, Callable
import re

from app.application.errors import InvalidFileContentError

RowsParser = Callable[[list[Any]], tuple[list[Any], int]]
TabularRowsParser = Callable[[list[Any], Any | None], tuple[list[Any], int]]

_DATE_PATTERN = re.compile(r"\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b")
_AMOUNT_PATTERN = re.compile(r"[\-+]?\d{1,3}(?:[.\s]\d{3})*,\d{2}\b|[\-+]?\d+\.\d{2}\b")


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
) -> str:
    line_texts = _resolve_line_texts(lines)
    joined = "\n".join(line_texts)
    has_date_like = bool(_DATE_PATTERN.search(joined))
    has_amount_like = bool(_AMOUNT_PATTERN.search(joined))
    missing_signals: list[str] = []
    if not has_date_like:
        missing_signals.append("date_pattern")
    if not has_amount_like:
        missing_signals.append("amount_pattern")
    if inline_candidates == 0 and tabular_candidates == 0 and columnar_candidates == 0:
        missing_signals.append("transaction_row_pattern")
    detail_suffix = (
        " diagnostics:"
        f" has_date_like={int(has_date_like)}"
        f" has_amount_like={int(has_amount_like)}"
        f" inline_candidates={inline_candidates}"
        f" tabular_candidates={tabular_candidates}"
        f" columnar_candidates={columnar_candidates}"
        f" missing_signals={','.join(sorted(set(missing_signals)))}"
    )
    return base_message + detail_suffix


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
        inline_rows, inline_candidates = parse_inline_rows(lines)
        inline_transactions_count = len(inline_rows)
        tabular_rows, tabular_candidates = parse_tabular_rows(lines, layout_profile)
        tabular_transactions_count = len(tabular_rows)
        columnar_rows, columnar_candidates = parse_columnar_rows(lines)
        columnar_transactions_count = len(columnar_rows)
        grouped_transactions_count = len(grouped_rows)
        tabular_is_clearly_better_than_grouped = tabular_transactions_count >= grouped_transactions_count + 3

        if tabular_rows and tabular_is_clearly_better_than_grouped:
            return SelectedParserRows(
                selected_parser="tabular",
                rows=tabular_rows,
                inline_candidates=inline_candidates,
                inline_transactions_count=inline_transactions_count,
                tabular_candidates=tabular_candidates,
                columnar_candidates=columnar_candidates,
                tabular_transactions_count=tabular_transactions_count,
                columnar_transactions_count=columnar_transactions_count,
                selection_reason="tabular_preferred_over_grouped_on_row_count_gap",
                inline_decision="not_selected_grouped_overridden",
                tabular_decision="selected_on_grouped_override_row_count_gap",
                columnar_decision="no_rows" if not columnar_rows else "not_selected_tabular_priority",
            )

        return SelectedParserRows(
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
        raise InvalidFileContentError(
            _build_pdf_pattern_failure_detail(
                base_message="PDF text was extracted, but transactions are in an unsupported table layout.",
                lines=lines,
                inline_candidates=inline_candidates,
                tabular_candidates=tabular_candidates,
                columnar_candidates=columnar_candidates,
            )
        )
    raise InvalidFileContentError(
        _build_pdf_pattern_failure_detail(
            base_message="PDF text was extracted, but no recognizable transaction row pattern was found.",
            lines=lines,
            inline_candidates=inline_candidates,
            tabular_candidates=tabular_candidates,
            columnar_candidates=columnar_candidates,
        )
    )
