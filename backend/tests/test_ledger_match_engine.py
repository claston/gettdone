from app.application.ledger_match_engine import (
    match_exact_then_date_tolerance_then_description_similarity_1to1,
)
from app.application.models import NormalizedTransaction


def test_match_pipeline_1to1_matches_exact_and_date_tolerance() -> None:
    bank_rows = [
        NormalizedTransaction(date="2026-04-01", description="A", amount=-100.0, type="outflow"),
        NormalizedTransaction(date="2026-04-01", description="B", amount=50.0, type="inflow"),
        NormalizedTransaction(date="2026-04-02", description="C", amount=-20.0, type="outflow"),
    ]
    sheet_rows = [
        NormalizedTransaction(date="2026-04-01", description="X", amount=-100.0, type="outflow"),
        NormalizedTransaction(date="2026-04-02", description="Y", amount=-20.0, type="outflow"),
        NormalizedTransaction(date="2026-04-03", description="Z", amount=50.0, type="inflow"),
    ]

    result = match_exact_then_date_tolerance_then_description_similarity_1to1(
        bank_rows=bank_rows,
        sheet_rows=sheet_rows,
    )

    assert result.exact_matches_count == 2
    assert result.date_tolerance_matches_count == 1
    assert result.description_similarity_matches_count == 0
    assert result.total_matches_count == 3
    assert result.bank_unmatched_count == 0
    assert result.sheet_unmatched_count == 0


def test_match_pipeline_1to1_does_not_reuse_sheet_row() -> None:
    bank_rows = [
        NormalizedTransaction(date="2026-04-01", description="A", amount=-100.0, type="outflow"),
        NormalizedTransaction(date="2026-04-01", description="B", amount=-100.0, type="outflow"),
    ]
    sheet_rows = [
        NormalizedTransaction(date="2026-04-01", description="X", amount=-100.0, type="outflow"),
    ]

    result = match_exact_then_date_tolerance_then_description_similarity_1to1(
        bank_rows=bank_rows,
        sheet_rows=sheet_rows,
    )

    assert result.exact_matches_count == 1
    assert result.date_tolerance_matches_count == 0
    assert result.description_similarity_matches_count == 0
    assert result.total_matches_count == 1
    assert result.bank_unmatched_count == 1
    assert result.sheet_unmatched_count == 0


def test_match_pipeline_1to1_matches_within_plus_or_minus_two_days() -> None:
    bank_rows = [
        NormalizedTransaction(date="2026-04-03", description="A", amount=-100.0, type="outflow"),
        NormalizedTransaction(date="2026-04-10", description="B", amount=200.0, type="inflow"),
    ]
    sheet_rows = [
        NormalizedTransaction(date="2026-04-01", description="X", amount=-100.0, type="outflow"),
        NormalizedTransaction(date="2026-04-12", description="Y", amount=200.0, type="inflow"),
    ]

    result = match_exact_then_date_tolerance_then_description_similarity_1to1(
        bank_rows=bank_rows,
        sheet_rows=sheet_rows,
    )

    assert result.exact_matches_count == 0
    assert result.date_tolerance_matches_count == 2
    assert result.description_similarity_matches_count == 0
    assert result.total_matches_count == 2
    assert result.bank_unmatched_count == 0
    assert result.sheet_unmatched_count == 0
    assert result.matches[0].match_rule == "date_tolerance"
    assert result.matches[0].reason == "matched_equal_amount_within_2_days"


def test_match_pipeline_1to1_uses_description_similarity_after_exact_and_tolerance() -> None:
    bank_rows = [
        NormalizedTransaction(
            date="2026-04-12",
            description="PAGAMENTO FORNECEDOR ALFA LTDA",
            amount=-980.0,
            type="outflow",
        ),
        NormalizedTransaction(
            date="2026-04-10",
            description="RECEBIMENTO CLIENTE BETA",
            amount=1200.0,
            type="inflow",
        ),
    ]
    sheet_rows = [
        NormalizedTransaction(
            date="2026-04-01",
            description="FORNECEDOR ALFA",
            amount=-980.0,
            type="outflow",
        ),
        NormalizedTransaction(
            date="2026-04-10",
            description="CLIENTE BETA",
            amount=1200.0,
            type="inflow",
        ),
    ]

    result = match_exact_then_date_tolerance_then_description_similarity_1to1(
        bank_rows=bank_rows,
        sheet_rows=sheet_rows,
    )

    assert result.exact_matches_count == 1
    assert result.date_tolerance_matches_count == 0
    assert result.description_similarity_matches_count == 1
    assert result.total_matches_count == 2
    assert result.bank_unmatched_count == 0
    assert result.sheet_unmatched_count == 0
    assert result.matches[1].match_rule == "description_similarity"
    assert result.matches[1].reason == "matched_equal_amount_with_similar_description"


def test_match_pipeline_1to1_does_not_match_description_similarity_below_threshold() -> None:
    bank_rows = [
        NormalizedTransaction(
            date="2026-04-12",
            description="PAGAMENTO FORNECEDOR ALFA LTDA",
            amount=-980.0,
            type="outflow",
        ),
    ]
    sheet_rows = [
        NormalizedTransaction(
            date="2026-04-01",
            description="TARIFA BANCARIA MENSAL",
            amount=-980.0,
            type="outflow",
        ),
    ]

    result = match_exact_then_date_tolerance_then_description_similarity_1to1(
        bank_rows=bank_rows,
        sheet_rows=sheet_rows,
    )

    assert result.exact_matches_count == 0
    assert result.date_tolerance_matches_count == 0
    assert result.description_similarity_matches_count == 0
    assert result.total_matches_count == 0
    assert result.bank_unmatched_count == 1
    assert result.sheet_unmatched_count == 1
