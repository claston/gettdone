import pytest

from app.application.errors import InvalidFileContentError
from app.application.normalization.pdf_parser_selection import select_parsed_rows


def test_select_parsed_rows_prioritizes_grouped_when_available() -> None:
    result = select_parsed_rows(
        lines=["line"],
        grouped_rows=["grouped-row"],
        layout_profile=None,
        parse_inline_rows=lambda _: (["inline-row"], 1),
        parse_tabular_rows=lambda _lines, _profile: (["tabular-row"], 1),
        parse_columnar_rows=lambda _: (["columnar-row"], 1),
    )

    assert result.selected_parser == "grouped"
    assert result.rows == ["grouped-row"]
    assert result.inline_candidates == 1
    assert result.inline_transactions_count == 1
    assert result.tabular_candidates == 1
    assert result.columnar_candidates == 1
    assert result.tabular_transactions_count == 1
    assert result.columnar_transactions_count == 1
    assert result.selection_reason == "grouped_rows_available"
    assert result.inline_decision == "not_selected_grouped_priority"
    assert result.tabular_decision == "not_selected_grouped_priority"
    assert result.columnar_decision == "not_selected_grouped_priority"


def test_select_parsed_rows_overrides_grouped_when_tabular_has_large_row_count_gap() -> None:
    result = select_parsed_rows(
        lines=["line"],
        grouped_rows=["grouped-1", "grouped-2"],
        layout_profile=object(),
        parse_inline_rows=lambda _: (["inline-row"], 1),
        parse_tabular_rows=lambda _lines, _profile: (
            ["tabular-1", "tabular-2", "tabular-3", "tabular-4", "tabular-5", "tabular-6"],
            6,
        ),
        parse_columnar_rows=lambda _: ([], 0),
    )

    assert result.selected_parser == "tabular"
    assert result.rows == ["tabular-1", "tabular-2", "tabular-3", "tabular-4", "tabular-5", "tabular-6"]
    assert result.selection_reason == "tabular_preferred_over_grouped_on_row_count_gap"
    assert result.inline_transactions_count == 1
    assert result.tabular_transactions_count == 6
    assert result.inline_decision == "not_selected_grouped_overridden"
    assert result.tabular_decision == "selected_on_grouped_override_row_count_gap"
    assert result.columnar_decision == "no_rows"


def test_select_parsed_rows_falls_back_to_tabular_and_preserves_inline_counters() -> None:
    result = select_parsed_rows(
        lines=["line"],
        grouped_rows=[],
        layout_profile=None,
        parse_inline_rows=lambda _: ([], 2),
        parse_tabular_rows=lambda _lines, _profile: (["tabular-row"], 3),
        parse_columnar_rows=lambda _: ([], 0),
    )

    assert result.selected_parser == "tabular"
    assert result.rows == ["tabular-row"]
    assert result.inline_candidates == 2
    assert result.inline_transactions_count == 0
    assert result.tabular_candidates == 3
    assert result.columnar_candidates == 0
    assert result.tabular_transactions_count == 1
    assert result.columnar_transactions_count == 0
    assert result.selection_reason == "tabular_rows_available_after_inline_empty"
    assert result.inline_decision == "no_rows"
    assert result.tabular_decision == "selected"
    assert result.columnar_decision == "no_rows"


def test_select_parsed_rows_selects_inline_and_exposes_decision_counters() -> None:
    result = select_parsed_rows(
        lines=["line"],
        grouped_rows=[],
        layout_profile=None,
        parse_inline_rows=lambda _: (["inline-row-a", "inline-row-b"], 3),
        parse_tabular_rows=lambda _lines, _profile: (["tabular-row"], 1),
        parse_columnar_rows=lambda _: (["columnar-row"], 1),
    )

    assert result.selected_parser == "inline"
    assert result.rows == ["inline-row-a", "inline-row-b"]
    assert result.inline_candidates == 3
    assert result.inline_transactions_count == 2
    assert result.tabular_candidates == 1
    assert result.columnar_candidates == 1
    assert result.tabular_transactions_count == 1
    assert result.columnar_transactions_count == 1
    assert result.selection_reason == "inline_rows_available_after_grouped_empty"
    assert result.inline_decision == "selected"
    assert result.tabular_decision == "not_selected_inline_priority"
    assert result.columnar_decision == "not_selected_inline_priority"


def test_select_parsed_rows_prefers_tabular_on_conflict_when_layout_profile_present() -> None:
    result = select_parsed_rows(
        lines=["line"],
        grouped_rows=[],
        layout_profile=object(),
        parse_inline_rows=lambda _: (["inline-row"], 2),
        parse_tabular_rows=lambda _lines, _profile: (["tabular-row-a", "tabular-row-b"], 2),
        parse_columnar_rows=lambda _: ([], 0),
    )

    assert result.selected_parser == "tabular"
    assert result.rows == ["tabular-row-a", "tabular-row-b"]
    assert result.selection_reason == "tabular_preferred_on_conflict_with_layout_profile"
    assert result.inline_transactions_count == 1
    assert result.tabular_transactions_count == 2
    assert result.inline_decision == "not_selected_conflict_lost_to_tabular"
    assert result.tabular_decision == "selected_on_conflict"
    assert result.columnar_decision == "no_rows"


def test_select_parsed_rows_prefers_tabular_when_grouped_only_adds_opening_balance_noise() -> None:
    class _Transaction:
        def __init__(self, description: str, date: str = "2022-01-03", amount: float = 1.0) -> None:
            self.description = description
            self.date = date
            self.amount = amount

    class _Row:
        def __init__(self, description: str, *, source_line: int, amount: float = 1.0) -> None:
            self.transaction = _Transaction(description=description, amount=amount)
            self.source_line = source_line

    grouped_rows = [
        _Row("SALDO ANTERIOR", source_line=20, amount=140.63),
        _Row("093303 CR VD CART", source_line=24, amount=4.93),
        _Row("671303 CR VD CART Data Mov. Nr. Doc. Historico Valor Saldo", source_line=48, amount=492.63),
    ]
    tabular_rows = [
        _Row("093303 CR VD CART", source_line=24, amount=4.93),
        _Row("671303 CR VD CART", source_line=48, amount=492.63),
    ]

    result = select_parsed_rows(
        lines=["line"],
        grouped_rows=grouped_rows,
        layout_profile=object(),
        parse_inline_rows=lambda _: ([], 0),
        parse_tabular_rows=lambda _lines, _profile: (tabular_rows, 2),
        parse_columnar_rows=lambda _: ([], 0),
    )

    assert result.selected_parser == "tabular"
    assert result.rows == tabular_rows
    assert result.selection_reason == "tabular_preferred_over_grouped_opening_balance_noise"
    assert result.inline_decision == "not_selected_grouped_overridden"
    assert result.tabular_decision == "selected_on_grouped_override_opening_balance_noise"
    assert result.columnar_decision == "no_rows"


def test_select_parsed_rows_prefers_tabular_when_inline_only_adds_opening_balance_noise() -> None:
    class _InlineRow:
        def __init__(self, description: str) -> None:
            self.description = description

    class _Transaction:
        def __init__(self, description: str, date: str = "2023-07-04", amount: float = 0.01) -> None:
            self.description = description
            self.date = date
            self.amount = amount

    class _TabularRow:
        def __init__(self, description: str, *, source_line: int) -> None:
            self.transaction = _Transaction(description=description)
            self.source_line = source_line

    inline_rows = [
        _InlineRow("SALDO ANTERIOR"),
        _InlineRow("APROPRIACAO DE CM"),
        _InlineRow("RESGATE DE APLICACAO FINANCEIRA"),
    ]
    tabular_rows = [
        _TabularRow("APROPRIACAO DE CM", source_line=20),
        _TabularRow("RESGATE DE APLICACAO FINANCEIRA", source_line=21),
    ]

    result = select_parsed_rows(
        lines=["line"],
        grouped_rows=[],
        layout_profile=object(),
        parse_inline_rows=lambda _: (inline_rows, 3),
        parse_tabular_rows=lambda _lines, _profile: (tabular_rows, 2),
        parse_columnar_rows=lambda _: ([], 0),
    )

    assert result.selected_parser == "tabular"
    assert result.rows == tabular_rows
    assert result.selection_reason == "tabular_preferred_over_inline_opening_balance_noise"
    assert result.inline_decision == "not_selected_opening_balance_noise"
    assert result.tabular_decision == "selected_on_inline_opening_balance_noise"
    assert result.columnar_decision == "no_rows"


def test_select_parsed_rows_keeps_inline_on_conflict_without_layout_profile() -> None:
    result = select_parsed_rows(
        lines=["line"],
        grouped_rows=[],
        layout_profile=None,
        parse_inline_rows=lambda _: (["inline-row-a", "inline-row-b"], 2),
        parse_tabular_rows=lambda _lines, _profile: (["tabular-row"], 1),
        parse_columnar_rows=lambda _: ([], 0),
    )

    assert result.selected_parser == "inline"
    assert result.rows == ["inline-row-a", "inline-row-b"]
    assert result.selection_reason == "inline_rows_available_after_grouped_empty"
    assert result.inline_transactions_count == 2
    assert result.tabular_transactions_count == 1
    assert result.inline_decision == "selected"
    assert result.tabular_decision == "not_selected_inline_priority"
    assert result.columnar_decision == "no_rows"


def test_select_parsed_rows_prefers_tabular_on_large_row_count_gap_without_layout_profile() -> None:
    result = select_parsed_rows(
        lines=["line"],
        grouped_rows=[],
        layout_profile=None,
        parse_inline_rows=lambda _: (["inline-a", "inline-b", "inline-c"], 3),
        parse_tabular_rows=lambda _lines, _profile: (
            ["tabular-1", "tabular-2", "tabular-3", "tabular-4", "tabular-5", "tabular-6", "tabular-7"],
            7,
        ),
        parse_columnar_rows=lambda _: ([], 0),
    )

    assert result.selected_parser == "tabular"
    assert result.selection_reason == "tabular_preferred_on_row_count_gap"
    assert result.inline_decision == "not_selected_row_count_gap"
    assert result.tabular_decision == "selected_on_row_count_gap"


def test_select_parsed_rows_raises_unsupported_layout_when_candidates_exist_without_rows() -> None:
    with pytest.raises(InvalidFileContentError, match="unsupported table layout") as exc_info:
        select_parsed_rows(
            lines=["line"],
            grouped_rows=[],
            layout_profile=None,
            parse_inline_rows=lambda _: ([], 1),
            parse_tabular_rows=lambda _lines, _profile: ([], 0),
            parse_columnar_rows=lambda _: ([], 0),
        )
    assert "inline_candidates=1" in str(exc_info.value)
    assert "missing_signals=" in str(exc_info.value)
    assert "date_pattern" in str(exc_info.value)
    assert "amount_pattern" in str(exc_info.value)


def test_select_parsed_rows_raises_no_pattern_when_no_candidates_exist() -> None:
    with pytest.raises(InvalidFileContentError, match="no recognizable transaction row pattern") as exc_info:
        select_parsed_rows(
            lines=["line"],
            grouped_rows=[],
            layout_profile=None,
            parse_inline_rows=lambda _: ([], 0),
            parse_tabular_rows=lambda _lines, _profile: ([], 0),
            parse_columnar_rows=lambda _: ([], 0),
        )
    assert "inline_candidates=0" in str(exc_info.value)
    assert "missing_signals=" in str(exc_info.value)
    assert "date_pattern" in str(exc_info.value)
    assert "amount_pattern" in str(exc_info.value)
    assert "transaction_row_pattern" in str(exc_info.value)


def test_select_parsed_rows_diagnostics_use_the_same_spaced_month_date_shape_as_parser() -> None:
    with pytest.raises(InvalidFileContentError) as exc_info:
        select_parsed_rows(
            lines=["01 / jul TRANSFERENCIA 1.234,56"],
            grouped_rows=[],
            layout_profile=None,
            parse_inline_rows=lambda _: ([], 0),
            parse_tabular_rows=lambda _lines, _profile: ([], 0),
            parse_columnar_rows=lambda _: ([], 0),
        )

    detail = str(exc_info.value)
    assert "has_date_like=1" in detail
    assert "has_amount_like=1" in detail
    assert "missing_signals=transaction_row_pattern" in detail
