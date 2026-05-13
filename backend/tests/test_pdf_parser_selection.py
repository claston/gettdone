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
    assert result.inline_candidates == 0
    assert result.inline_transactions_count == 0
    assert result.tabular_candidates == 0
    assert result.columnar_candidates == 0


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


def test_select_parsed_rows_raises_unsupported_layout_when_candidates_exist_without_rows() -> None:
    with pytest.raises(InvalidFileContentError, match="unsupported table layout"):
        select_parsed_rows(
            lines=["line"],
            grouped_rows=[],
            layout_profile=None,
            parse_inline_rows=lambda _: ([], 1),
            parse_tabular_rows=lambda _lines, _profile: ([], 0),
            parse_columnar_rows=lambda _: ([], 0),
        )


def test_select_parsed_rows_raises_no_pattern_when_no_candidates_exist() -> None:
    with pytest.raises(InvalidFileContentError, match="no recognizable transaction row pattern"):
        select_parsed_rows(
            lines=["line"],
            grouped_rows=[],
            layout_profile=None,
            parse_inline_rows=lambda _: ([], 0),
            parse_tabular_rows=lambda _lines, _profile: ([], 0),
            parse_columnar_rows=lambda _: ([], 0),
        )
