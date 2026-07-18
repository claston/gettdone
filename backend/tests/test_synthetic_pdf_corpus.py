from pathlib import Path
from shutil import which

import pytest

from app.application.parsers.pdf.text_extraction import read_native_pdf_page_texts, read_pdf_page_count
from app.application.pdf_parser import parse_pdf_transactions
from synthetic_pdf_corpus.catalog import load_scenarios
from synthetic_pdf_corpus.evaluator import evaluate_transactions
from synthetic_pdf_corpus.generator import generate_pdf
from synthetic_pdf_corpus.models import ExpectedTransaction
from synthetic_pdf_corpus.runner import evaluate_pdf, summarize_results

_SCENARIOS_DIR = Path(__file__).parent / "fixtures" / "pdf_scenarios"


def test_synthetic_pdf_catalog_loads_versioned_modular_scenarios() -> None:
    scenarios = load_scenarios(_SCENARIOS_DIR)

    assert len(scenarios) == 5
    assert len({scenario.scenario_id for scenario in scenarios}) == len(scenarios)
    assert all(scenario.schema_version == 1 for scenario in scenarios)
    assert all("native_text" in scenario.variants for scenario in scenarios)
    assert all("scanned" in scenario.variants for scenario in scenarios)
    assert all(scenario.expected.transactions for scenario in scenarios)
    assert sum(scenario.expected.enforce_success for scenario in scenarios) == 3
    assert all(
        scenario.expected.enforce_success or scenario.expected.known_gap
        for scenario in scenarios
    )


@pytest.mark.parametrize("scenario", load_scenarios(_SCENARIOS_DIR), ids=lambda item: item.scenario_id)
def test_native_text_pdf_generator_preserves_pages_and_text(scenario) -> None:
    raw_pdf = generate_pdf(scenario, variant="native_text")

    extracted_pages = read_native_pdf_page_texts(raw_pdf)

    assert raw_pdf.startswith(b"%PDF")
    assert read_pdf_page_count(raw_pdf) == len(scenario.pages)
    assert len(extracted_pages) == len(scenario.pages)
    for expected_lines, extracted_text in zip(scenario.pages, extracted_pages, strict=True):
        assert extracted_text.splitlines() == list(expected_lines)


@pytest.mark.parametrize("scenario", load_scenarios(_SCENARIOS_DIR), ids=lambda item: item.scenario_id)
def test_scanned_pdf_generator_has_pages_without_native_text(scenario) -> None:
    raw_pdf = generate_pdf(scenario, variant="scanned")

    assert raw_pdf.startswith(b"%PDF")
    assert read_pdf_page_count(raw_pdf) == len(scenario.pages)
    assert read_native_pdf_page_texts(raw_pdf) == []


def test_transaction_evaluator_reports_recall_precision_and_differences() -> None:
    expected = (
        ExpectedTransaction(
            date="2026-07-15",
            description_contains="PIX RECEBIDO",
            amount=100.0,
            transaction_type="inflow",
        ),
        ExpectedTransaction(
            date="2026-07-16",
            description_contains="PAGAMENTO",
            amount=-20.0,
            transaction_type="outflow",
        ),
    )
    actual = [
        type("Transaction", (), {"date": "2026-07-15", "description": "PIX RECEBIDO CLIENTE", "amount": 100.0, "type": "inflow"})(),
        type("Transaction", (), {"date": "2026-07-17", "description": "TARIFA", "amount": -5.0, "type": "outflow"})(),
    ]

    report = evaluate_transactions(expected, actual, max_false_positives=0)

    assert report.matched_count == 1
    assert report.expected_count == 2
    assert report.actual_count == 2
    assert report.false_positive_count == 1
    assert report.recall == 0.5
    assert report.precision == 0.5
    assert not report.success
    assert report.missing_transactions == (expected[1],)
    assert len(report.unexpected_transactions) == 1


def test_corpus_runner_exposes_parser_decision_and_blocking_policy() -> None:
    scenario = next(
        item for item in load_scenarios(_SCENARIOS_DIR) if item.scenario_id == "inline_signed_values"
    )
    raw_pdf = generate_pdf(scenario, variant="native_text")

    result = evaluate_pdf(
        scenario,
        variant="native_text",
        raw_pdf=raw_pdf,
        parse_pdf=parse_pdf_transactions,
    )

    assert result.success
    assert not result.blocks_ci
    assert result.selected_parser
    assert result.parser_selection_reason
    assert result.parser_matches_expectation
    assert result.evaluation is not None
    assert result.evaluation.recall == 1.0
    assert result.evaluation.precision == 1.0


def test_corpus_summary_aggregates_quantitative_quality_metrics() -> None:
    scenarios = load_scenarios(_SCENARIOS_DIR)
    results = [
        evaluate_pdf(
            scenario,
            variant="native_text",
            raw_pdf=generate_pdf(scenario, variant="native_text"),
            parse_pdf=parse_pdf_transactions,
        )
        for scenario in scenarios
    ]

    summary = summarize_results(results)

    assert summary.evaluated_count == 5
    assert summary.passed_count == 3
    assert summary.known_gap_count == 2
    assert summary.blocking_failure_count == 0
    assert summary.expected_transaction_count == 12
    assert summary.actual_transaction_count == 11
    assert summary.matched_transaction_count == 10
    assert summary.false_positive_count == 1
    assert summary.recall == 0.833333
    assert summary.precision == 0.909091


@pytest.mark.parametrize("scenario", load_scenarios(_SCENARIOS_DIR), ids=lambda item: item.scenario_id)
def test_native_text_synthetic_corpus_runs_through_real_pdf_pipeline(scenario) -> None:
    raw_pdf = generate_pdf(scenario, variant="native_text")

    parse_result = parse_pdf_transactions(raw_pdf)
    report = evaluate_transactions(
        scenario.expected.transactions,
        parse_result.transactions,
        max_false_positives=scenario.expected.max_false_positives,
    )

    if not report.success and not scenario.expected.enforce_success:
        pytest.xfail(f"known gap: {scenario.expected.known_gap}; {report.summary}")
    assert report.success, report.summary
    if scenario.expected.selected_parser:
        assert parse_result.parse_metrics["selected_parser"] == scenario.expected.selected_parser


@pytest.mark.pdf_ocr
@pytest.mark.skipif(which("tesseract") is None, reason="Tesseract binary is not installed")
def test_scanned_synthetic_pdf_runs_through_real_ocr_pipeline(monkeypatch) -> None:
    scenario = next(
        item for item in load_scenarios(_SCENARIOS_DIR) if item.scenario_id == "inline_signed_values"
    )
    raw_pdf = generate_pdf(scenario, variant="scanned")
    monkeypatch.setenv("PDF_OCR_ENABLED", "1")
    monkeypatch.setenv("PDF_OCR_ENGINE", "tesseract")
    monkeypatch.setenv("PDF_OCR_LANG", "eng")

    parse_result = parse_pdf_transactions(raw_pdf)
    report = evaluate_transactions(
        scenario.expected.transactions,
        parse_result.transactions,
        max_false_positives=scenario.expected.max_false_positives,
    )

    assert report.success, report.summary
