"""Utilities for generating and evaluating deterministic synthetic PDF statements."""

from synthetic_pdf_corpus.catalog import load_scenario, load_scenarios
from synthetic_pdf_corpus.evaluator import evaluate_transactions
from synthetic_pdf_corpus.generator import generate_pdf
from synthetic_pdf_corpus.models import EvaluationReport, ExpectedTransaction, SyntheticPdfScenario

__all__ = [
    "EvaluationReport",
    "ExpectedTransaction",
    "SyntheticPdfScenario",
    "evaluate_transactions",
    "generate_pdf",
    "load_scenario",
    "load_scenarios",
]
