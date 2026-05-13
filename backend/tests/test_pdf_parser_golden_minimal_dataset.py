import pytest

from app.application import pdf_parser as pdf_parser_module
from app.application.errors import InvalidFileContentError
from app.application.pdf_parser import parse_pdf_transactions
from tests.fixtures.pdf_golden_samples import (
    PDF_GOLDEN_MINIMAL_EXPECTATIONS,
    PDF_GOLDEN_MINIMAL_SCENARIOS,
)

pytestmark = pytest.mark.pdf_golden


@pytest.mark.parametrize("scenario_name", ["grouped", "inline", "tabular", "columnar"])
def test_pdf_parser_golden_minimal_dataset_stability(monkeypatch, scenario_name: str) -> None:
    scenario = PDF_GOLDEN_MINIMAL_SCENARIOS[scenario_name]
    expected = PDF_GOLDEN_MINIMAL_EXPECTATIONS[scenario_name]
    result = _run_pdf_parser_scenario(
        monkeypatch=monkeypatch,
        sample_text=scenario["sample_text"],
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


def _run_pdf_parser_scenario(
    *,
    monkeypatch,
    sample_text: str,
    layout_override: pdf_parser_module.PdfLayoutInference | None,
):
    monkeypatch.setattr(pdf_parser_module, "_extract_pdf_page_texts", lambda raw_bytes: [sample_text])
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
