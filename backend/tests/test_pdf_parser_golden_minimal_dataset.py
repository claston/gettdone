import pytest

from app.application import pdf_parser as pdf_parser_module
from app.application.errors import InvalidFileContentError
from app.application.normalization.text import normalize_upper_text
from app.application.pdf_parser import parse_pdf_transactions
from tests.fixtures.pdf_golden_samples import (
    PDF_GOLDEN_MINIMAL_EXPECTATIONS,
    PDF_GOLDEN_MINIMAL_SCENARIOS,
)

pytestmark = pytest.mark.pdf_golden


def test_pdf_golden_minimal_catalog_matches_expectations_keys() -> None:
    assert set(PDF_GOLDEN_MINIMAL_SCENARIOS.keys()) == set(PDF_GOLDEN_MINIMAL_EXPECTATIONS.keys())
    for scenario_name, scenario in PDF_GOLDEN_MINIMAL_SCENARIOS.items():
        sample_text = scenario.get("sample_text")
        sample_pages = scenario.get("sample_pages")
        assert sample_text is not None or sample_pages is not None, scenario_name
        if sample_text is not None:
            assert isinstance(sample_text, str), scenario_name
            assert sample_text.strip(), scenario_name
            assert any(char.isdigit() for char in sample_text), scenario_name
        if sample_pages is not None:
            assert isinstance(sample_pages, list), scenario_name
            assert sample_pages, scenario_name
            for page_text in sample_pages:
                assert isinstance(page_text, str), scenario_name
                assert page_text.strip(), scenario_name
                assert any(char.isdigit() for char in page_text), scenario_name
    for scenario_name, expected in PDF_GOLDEN_MINIMAL_EXPECTATIONS.items():
        first_transaction = expected["first_transaction"]
        assert first_transaction["date"], scenario_name
        assert first_transaction["type"] in {"inflow", "outflow"}, scenario_name
        assert isinstance(first_transaction["amount"], float), scenario_name
        assert isinstance(first_transaction["source_page"], int), scenario_name
        assert first_transaction["source_page"] >= 1, scenario_name
        assert isinstance(first_transaction["source_line"], int), scenario_name
        assert first_transaction["source_line"] >= 1, scenario_name
        if "description" in first_transaction:
            assert first_transaction["description"].strip(), scenario_name


@pytest.mark.parametrize("scenario_name", sorted(PDF_GOLDEN_MINIMAL_SCENARIOS.keys()))
def test_pdf_parser_golden_minimal_dataset_stability(monkeypatch, scenario_name: str) -> None:
    scenario = PDF_GOLDEN_MINIMAL_SCENARIOS[scenario_name]
    expected = PDF_GOLDEN_MINIMAL_EXPECTATIONS[scenario_name]
    result = _run_pdf_parser_scenario(
        monkeypatch=monkeypatch,
        sample_text=scenario.get("sample_text"),
        sample_pages=scenario.get("sample_pages"),
        layout_override=_build_layout_override(layout_name=scenario["layout_name"]),
    )

    assert len(result.transactions) == expected["transactions_count"], scenario_name
    assert result.parse_metrics["selected_parser"] == expected["selected_parser"], scenario_name
    assert result.parse_metrics[
        f"canonical_source_parser_{expected['selected_parser']}_count"
    ] == expected["transactions_count"], (
        scenario_name
    )
    assert result.parse_metrics["canonical_source_parser_types"] == expected["selected_parser"], scenario_name
    assert result.parse_metrics["canonical_transactions_count"] == expected["transactions_count"], scenario_name
    assert result.parse_metrics["inline_candidates_count"] == expected["inline_candidates_count"], scenario_name
    assert result.parse_metrics["balance_consistency_checked"] == expected["balance_consistency_checked"], scenario_name
    assert result.parse_metrics["balance_consistency_failed"] == expected["balance_consistency_failed"], scenario_name
    assert result.parse_metrics["canonical_source_parser_types_count"] == 1, scenario_name
    assert result.parse_metrics["canonical_source_parser_types_list"] == expected["selected_parser"], scenario_name
    assert result.parse_metrics["canonical_warning_types_count"] >= 0, scenario_name
    assert result.parse_metrics["canonical_warning_count"] >= 0, scenario_name

    first_expected = expected["first_transaction"]
    first_transaction = result.transactions[0]
    first_canonical = result.canonical_transactions[0]
    assert first_transaction.date == first_expected["date"], scenario_name
    assert first_transaction.amount == first_expected["amount"], scenario_name
    assert first_transaction.type == first_expected["type"], scenario_name
    assert first_canonical.source_page == first_expected["source_page"], scenario_name
    assert first_canonical.source_line == first_expected["source_line"], scenario_name
    expected_description = first_expected.get("description")
    if expected_description is not None:
        assert normalize_upper_text(first_transaction.description) == normalize_upper_text(expected_description), scenario_name


def test_pdf_parser_golden_negative_unsupported_layout_message(monkeypatch) -> None:
    sample_text = "\n".join(
        [
            "10/04 1,00",
            "11/04 2,00",
        ]
    )
    with pytest.raises(InvalidFileContentError, match="unsupported table layout"):
        _run_pdf_parser_scenario(
            monkeypatch=monkeypatch,
            sample_text=sample_text,
            layout_override=None,
        )


def test_pdf_parser_golden_negative_no_pattern_message(monkeypatch) -> None:
    sample_text = "\n".join(
        [
            "RESUMO DO PERIODO",
            "SALDO DISPONIVEL",
            "SEM MOVIMENTACOES",
        ]
    )
    with pytest.raises(InvalidFileContentError, match="no recognizable transaction row pattern"):
        _run_pdf_parser_scenario(
            monkeypatch=monkeypatch,
            sample_text=sample_text,
            layout_override=None,
        )


def test_pdf_parser_golden_multi_page_traceability_keeps_source_coordinates(monkeypatch) -> None:
    page_one = "\n".join(
        [
            "BANCO INTER",
            "RESUMO",
        ]
    )
    page_two = "\n".join(
        [
            "16/04 TED recebida 90,00",
            "17/04 Tarifa pacote 12,00",
        ]
    )
    result = _run_pdf_parser_scenario(
        monkeypatch=monkeypatch,
        sample_pages=[page_one, page_two],
        layout_override=None,
    )

    assert result.parse_metrics["page_count"] == 2
    assert result.parse_metrics["selected_parser"] == "inline"
    assert len(result.transactions) == 2
    assert result.transactions[0].amount == 90.0
    assert result.transactions[1].amount == -12.0
    assert result.canonical_transactions[0].source_page == 2
    assert result.canonical_transactions[0].source_line == 1
    assert result.canonical_transactions[1].source_page == 2
    assert result.canonical_transactions[1].source_line == 2


def _run_pdf_parser_scenario(
    *,
    monkeypatch,
    sample_text: str | None = None,
    sample_pages: list[str] | None = None,
    layout_override: pdf_parser_module.PdfLayoutInference | None,
):
    if sample_pages is not None:
        pages = sample_pages
    elif sample_text is not None:
        pages = [sample_text]
    else:
        raise ValueError("sample_text or sample_pages must be provided")
    monkeypatch.setattr(pdf_parser_module, "_extract_pdf_page_texts", lambda raw_bytes: pages)
    if layout_override is not None:
        monkeypatch.setattr(pdf_parser_module, "infer_pdf_layout", lambda text: layout_override)
    return parse_pdf_transactions(b"%PDF synthetic")


def _build_layout_override(layout_name: str | None) -> pdf_parser_module.PdfLayoutInference | None:
    if not layout_name:
        return None
    return pdf_parser_module.PdfLayoutInference(
        layout_name=layout_name,
        confidence=0.9,
        used_fallback=False,
    )
