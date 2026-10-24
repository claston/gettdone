"""The gate rejects dropped successes, lost duplicates and wrong document identity."""
import runpy
from copy import deepcopy
from pathlib import Path

import pytest

compare = runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts/check_pdf_compatibility.py"))["compare_reports"]
pytestmark = pytest.mark.pdf_acceptance


@pytest.mark.parametrize("mutation", ["refused", "lost_duplicate", "changed_amount", "changed_date", "wrong_identity", "lost_review"])
def test_gate_rejects_regressions_even_when_the_conversion_completes(mutation):
    rows = [["2026-08-01", -20], ["2026-08-01", -20]]
    cases = [{"id": "fixture", "expected": {"movements": rows, "document_type": "bank_statement", "review": True}}]
    good = {"fixture": {"success": True, "movements": rows, "document_type": "bank_statement", "review": True}}
    bad = deepcopy(good)
    item = bad["fixture"]
    if mutation == "refused":
        item["success"] = False
    if mutation == "lost_duplicate":
        item["movements"].pop()
    if mutation == "changed_amount":
        item["movements"][0][1] = 20
    if mutation == "changed_date":
        item["movements"][0][0] = "2026-08-02"
    if mutation == "wrong_identity":
        item["document_type"] = "investment_statement"
    if mutation == "lost_review":
        item["review"] = False
    assert compare(cases, good, bad)["errors"]


def test_source_correction_requires_documented_baseline_change():
    case = {"id": "fixture", "expected": {"movements": [["2026-08-01", 20]]}}
    baseline = {"fixture": {"success": True, "movements": [["2026-08-01", 200]], "review": False}}
    current = {"fixture": {"success": True, "movements": [["2026-08-01", 20]], "review": False}}
    assert compare([case], baseline, current)["errors"]
    case["correction_reason"] = "The independently transcribed source amount is 20.00, not 200.00."
    assert not compare([case], baseline, current)["errors"]
    current["fixture"]["movements"] = [["2026-08-01", 2]]
    assert compare([case], baseline, current)["errors"]
