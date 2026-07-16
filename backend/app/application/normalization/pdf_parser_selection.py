from dataclasses import dataclass
from typing import Any, Callable

from app.application.errors import InvalidFileContentError
from app.application.normalization.pdf_amount_tokens import contains_amount_like
from app.application.normalization.pdf_row_match_rules import contains_date_like
from app.application.normalization.pdf_tabular_profile_rules import (
    find_profile_tabular_amount_tokens,
    profile_date_formats,
)

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
            return SelectedParserRows(
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
    tabular_avoids_inline_opening_balance_noise = _should_prefer_tabular_over_inline_on_opening_balance_noise(
        inline_rows=inline_rows,
        tabular_rows=tabular_rows,
        layout_profile=layout_profile,
    )

    if inline_rows and tabular_rows and (tabular_is_clearly_better_than_inline or tabular_avoids_inline_opening_balance_noise):
        return SelectedParserRows(
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

    multiline_rows: list[Any] = []
    multiline_candidates = 0
    if parse_multiline_rows is not None:
        multiline_rows, multiline_candidates = parse_multiline_rows(lines, layout_profile)
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
