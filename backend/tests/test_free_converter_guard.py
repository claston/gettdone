"""OFX-67: prove the release gate detects silent financial regressions."""
import importlib.util
import json
from copy import deepcopy
from pathlib import Path

import pytest
from openpyxl import Workbook

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/free_converter_guard.py"
spec = importlib.util.spec_from_file_location("free_converter_guard", SCRIPT)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)
pytestmark = pytest.mark.pdf_acceptance


@pytest.mark.parametrize("path,value", [
    (("status",), "success"),
    (("rows", 0, "description"), "PIX DIFERENTE"),
    (("rows", 0, "amount_cents"), -2001),
    (("rows", 0, "date"), "2026-09-02"),
    (("rows", 0, "source_page"), 2),
    (("rows", 0, "transaction_id"), "changed"),
    (("rows", 0, "warnings"), []),
    (("closing_balance_cents",), 9999),
    (("ofx",), "<FITID>changed"),
    (("csv", 1, 2), "-20.01"),
    (("xlsx", "Conversao", 1, 2), "20.01"),
])
def test_strict_diff_identifies_exact_changed_path(path, value):
    old = {"status": "review", "rows": [{"description": "PIX", "amount_cents": -2000,
           "date": "2026-09-01", "source_page": 1, "transaction_id": "stable", "warnings": ["review"]}],
           "closing_balance_cents": 10000, "ofx": "<FITID>stable",
           "csv": [["date", "description", "amount"], ["2026-09-01", "PIX", "-20.0"]],
           "xlsx": {"Conversao": [["Data", "Historico", "Debito"], ["01-09-2026", "PIX", "20.0"]]}}
    new = deepcopy(old)
    target = new
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    differences = guard.diff_values(old, new)
    assert differences
    expected_path = "$" + "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in path)
    assert differences[0]["path"] == expected_path + ("[0]" if path[-1] == "warnings" else "")
    assert differences[0]["before"] != differences[0]["after"]


def test_order_duplicates_and_missing_cases_are_not_normalized_away():
    assert guard.diff_values([1, 2, 2], [2, 1, 2])
    assert guard.diff_values([1, 2, 2], [1, 2])
    assert guard.compare_snapshots({"a": {"success": False, "snapshot": {"status": "rejected"}}}, {})["errors"]
    assert guard.compare_snapshots({}, {})["errors"]


def test_rejection_to_acceptance_and_crash_on_rejected_fixture_fail_closed():
    old = {"a": {"success": False, "snapshot": {"status": "rejected"}}}
    assert guard.compare_snapshots(old, {"a": {"snapshot": {"status": "success"}}})["errors"]
    broken = deepcopy(old)
    broken["a"]["technical_failure"] = True
    assert guard.compare_snapshots(old, broken)["errors"]


def test_approval_is_bound_to_exact_values_revisions_and_owner_evidence():
    old = {"a": {"snapshot": {"amount_cents": 20000}}}
    new = {"a": {"snapshot": {"amount_cents": 2000}}}
    approval = {"difference": {"case": "a", "path": "$.amount_cents", "before": 20000, "after": 2000, "change": "changed"},
                "approved_by": "product-owner", "approval_reference": "review-url", "reason": "Source reads 20.00",
                "reference_commit": "reference", "candidate_app_tree": "tree"}
    kwargs = {"approvals": [approval], "reference_commit": "reference", "candidate_app_tree": "tree"}
    assert not guard.compare_snapshots(old, new, **kwargs)["errors"]
    assert guard.compare_snapshots(old, new, **{**kwargs, "candidate_app_tree": "another-tree"})["errors"]
    wrong = deepcopy(new)
    wrong["a"]["snapshot"]["amount_cents"] = 2001
    assert guard.compare_snapshots(old, wrong, **kwargs)["errors"]
    approval["approved_by"] = ""
    assert guard.compare_snapshots(old, new, **kwargs)["errors"]


def test_cent_conversion_is_exact_and_never_rounds_a_regression_away():
    assert guard.cents("-0.01") == -1
    assert guard.cents(1234.56) == 123456
    with pytest.raises(ValueError, match="cent"):
        guard.cents("20.001")


def test_frozen_geometry_drift_is_catalogued_only_between_known_different_platforms():
    old = {"a": {"snapshot": {"rows": [{"amount_cents": 2000, "source_page": 1,
           "source_regions": [{"page": 1, "left": 0.0825042019371225}]}]}}}
    new = deepcopy(old)
    new["a"]["snapshot"]["rows"][0]["source_regions"][0]["left"] = 0.08236974507820706
    frozen = {"platform": "win32", "cases": old}
    candidate = {"platform": "linux", "cases": new}
    comparison = guard.compare_frozen_snapshots(frozen, candidate)
    assert not comparison["errors"]
    assert comparison["platform_geometry_differences"][0]["path"] == "$.rows[0].source_regions[0].left"
    assert guard.compare_snapshots(old, new)["errors"]  # Git pre/post stays exact.
    assert guard.compare_frozen_snapshots(frozen, {**candidate, "platform": "win32"})["errors"]
    assert guard.compare_frozen_snapshots({"cases": old}, candidate)["errors"]


@pytest.mark.parametrize("field,value", [("left", 0.0845), ("page", 2), ("amount_cents", 2001), ("source_page", 2)])
def test_cross_platform_geometry_policy_preserves_financial_and_source_regression_gates(field, value):
    old = {"a": {"snapshot": {"rows": [{"amount_cents": 2000, "source_page": 1,
           "source_regions": [{"page": 1, "left": 0.0825}]}]}}}
    new = deepcopy(old)
    row = new["a"]["snapshot"]["rows"][0]
    (row["source_regions"][0] if field in {"left", "page"} else row)[field] = value
    assert guard.compare_frozen_snapshots({"platform": "win32", "cases": old}, {"platform": "linux", "cases": new})["errors"]


def test_csv_platform_newlines_preserve_every_financial_cell_and_duplicate():
    unix = 'date,description,amount\n2026-09-01,"PIX, CLIENTE",-0.01\n2026-09-01,"PIX, CLIENTE",-0.01\n'
    windows = unix.replace("\n", "\r\r\n")
    assert guard.snapshot_csv(unix) == guard.snapshot_csv(windows)
    assert len(guard.snapshot_csv(windows)) == 3
    assert guard.snapshot_csv(windows)[1][2] == "-0.01"


def test_workbook_snapshot_compares_cells_and_formulas_without_zip_timestamps(tmp_path):
    book = Workbook()
    book.active.append(["Data", "Histórico", "Valor"])
    book.active.append(["01-09-2026", "Transferência", -20.01])
    book.active.append([None, None, "=C2"])
    first = tmp_path / "first.xlsx"
    second = tmp_path / "second.xlsx"
    book.save(first)
    book.properties.modified = None
    book.save(second)
    assert guard.snapshot_workbook(first) == guard.snapshot_workbook(second)
    book.active["C2"] = -20.02
    book.save(second)
    assert guard.diff_values(guard.snapshot_workbook(first), guard.snapshot_workbook(second))
    book.close()


def test_ofx_only_empty_ledger_generation_date_is_dynamic():
    raw = "<LEDGERBAL>\n<DTASOF>20261101000000[-3:BRT]\n<BALAMT>150.00\n<FITID>stable"
    assert "<generation-date>" in guard.normalize_ofx(raw, empty=True)
    assert guard.normalize_ofx(raw, empty=False) == raw
    assert "<FITID>stable" in guard.normalize_ofx(raw, empty=True)


def test_performance_gate_checks_each_representation_and_resource_budget():
    base = {"environment": {"profile": "local"}, "repeat": 5, "process_peak_rss_bytes": 100,
            "groups": {"native": {"p95_ms": 100, "cpu_p95_ms": 100, "export_p95_ms": 100,
                                  "technical_failures": 0, "timeouts": 0}}}
    assert not guard.compare_performance(base, deepcopy(base))["errors"]
    for field in ("p95_ms", "cpu_p95_ms", "export_p95_ms", "technical_failures", "timeouts"):
        candidate = deepcopy(base)
        candidate["groups"]["native"][field] = 120
        assert guard.compare_performance(base, candidate)["errors"]
    candidate = deepcopy(base)
    candidate["process_peak_rss_bytes"] = 120
    assert guard.compare_performance(base, candidate)["errors"]
    candidate = deepcopy(base)
    candidate["repeat"] = 1
    assert guard.compare_performance(base, candidate)["errors"]
    candidate = deepcopy(base)
    candidate["environment"]["profile"] = "other"
    assert guard.compare_performance(base, candidate)["errors"]


def test_real_pipeline_exports_match_independent_oracle_and_frozen_baseline(tmp_path, monkeypatch):
    for name, value in {"TEXTRACT_ENABLED": "false", "PDF_OCR_ENABLED": "false", "AI_RECOVERY_MODE": "off",
                        "CANONICAL_LAYOUT_CAPTURE_ENABLED": "false", "PDF_MULTI_ACCOUNT_ENABLED": "true"}.items():
        monkeypatch.setenv(name, value)
    cases = [case for case in guard.extra_cases() if case["id"] in {
        "free_catalog_inline_signed_values", "free_long_multiline_transfer", "free_empty_statement",
        "free_invalid_pdf", "free_password_protected"}]
    output = tmp_path / "report.json"
    guard.worker(SCRIPT.parents[1] / "backend", cases, output)
    candidate = json.loads(output.read_text(encoding="utf-8"))
    actual = candidate["cases"]
    frozen = json.loads(guard.FROZEN_BASELINE.read_text(encoding="utf-8"))
    frozen["cases"] = {case["id"]: frozen["cases"][case["id"]] for case in cases}
    comparison = guard.compare_frozen_snapshots(frozen, candidate)
    assert not comparison["errors"], json.dumps(comparison, ensure_ascii=True, indent=2)
    for case in cases:
        assert not guard.source_checks(case, actual[case["id"]])
    case = next(case for case in cases if case["id"] == "free_catalog_inline_signed_values")
    bad = deepcopy(actual[case["id"]])
    bad["snapshot"]["artifacts"]["converted.csv"][1][2] = "1250.01"
    assert guard.source_checks(case, bad)
