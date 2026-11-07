"""Compare legacy review commands across Git revisions using OFX-67 snapshots.

Only synthetic fixtures are accepted. No baseline regeneration or diff approvals.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from shutil import copytree, ignore_patterns

from free_converter_guard import TIMING_FIELDS, compare_snapshots, normalize_ofx, snapshot_csv, snapshot_workbook

ROOT = Path(__file__).resolve().parents[1]


def worker(backend, report):
    sys.path[:0] = [str(backend), str(ROOT / "backend")]
    import app
    from app.application.default_conversion_pipeline import build_default_conversion_pipeline
    from app.application.errors import AnalysisEditConflictError, AnalysisNotFoundError, InvalidFileContentError
    from app.application.s3_analysis_storage import S3AnalysisStorage
    from app.application.source_regions import SourceRegion
    from app.application.storage_service import TempAnalysisStorage
    from tests.fixtures.positioned_cash_statements import positioned_pdf
    from tests.test_convert_edits_api import _build_analysis_data
    from tests.test_financial_document_events import report_pdf
    from tests.test_s3_analysis_storage import FakeS3Client
    from tests.test_statement_account_blocks import consolidated_pages

    assert Path(app.__file__).resolve().parent == (backend / "app").resolve(), "Wrong application revision imported"
    results = {}
    for remote in (False, True):
        for kind in ("stable", "positional", "zero", "no_balances", "empty", "multiconta", "specialized"):
            with tempfile.TemporaryDirectory(prefix="gettdone-review-case-") as temporary:
                root = Path(temporary)
                clock = [datetime(2026, 11, 2, 12, tzinfo=timezone.utc)]
                s3 = FakeS3Client()

                def storage():
                    options = {"root_dir": root, "now_provider": lambda: clock[0]}
                    return S3AnalysisStorage(**options, bucket="synthetic", s3_client=s3) if remote else TempAnalysisStorage(**options)

                data = _build_analysis_data("an_review_guard", first_amount=0 if kind == "zero" else -20)
                if kind in {"multiconta", "specialized"}:
                    raw = positioned_pdf(consolidated_pages()) if kind == "multiconta" else report_pdf("receivables")
                    data = build_default_conversion_pipeline().run(filename="synthetic.pdf", raw_bytes=raw,
                                                                   analysis_id=data.analysis_id).analysis_data
                else:
                    data.pdf_processing_metrics = {"page_count": 2, "ocr_balance_arithmetic_matches": 1}
                    for index, row in enumerate(data.preview_transactions):
                        if kind != "positional":
                            row.transaction_id, row.source_sequence = f"stable-{index}", index + 1
                        row.source_page, row.source_line = 1, index + 1
                        row.source_regions = [SourceRegion(1, 0.1, 0.2 + index * 0.1, 0.6, 0.03)]
                        if kind == "no_balances":
                            row.running_balance = None
                    if kind == "no_balances":
                        data.pdf_processing_metrics["balance_bounds_unavailable"] = 1
                    if kind == "empty":
                        data.preview_transactions, data.report_transactions = [], []
                        data.transactions_total = data.total_inflows = data.total_outflows = data.net_total = 0
                        data.opening_balance = data.closing_balance = 150
                        data.pdf_processing_metrics["statement_no_activity"] = 1
                # Compare review from identical inputs, including initial version.
                # Pipeline timings use the exhaustive OFX-67 dynamic catalogue.
                data.updated_at = clock[0].isoformat()
                data.pdf_processing_metrics = {k: v for k, v in (data.pdf_processing_metrics or {}).items() if k not in TIMING_FIELDS}
                active = storage()
                active.save_analysis(data)
                metadata = root / data.analysis_id / "analysis.json"
                key = ("s3" if remote else "local") + "/" + kind

                def capture(label, response):
                    artifacts = {}
                    for path in sorted(metadata.parent.iterdir()):
                        if path.suffix == ".xlsx":
                            artifacts[path.name] = snapshot_workbook(path)
                        elif path.suffix == ".csv":
                            artifacts[path.name] = snapshot_csv(path.read_text(encoding="utf-8"))
                        elif path.suffix == ".ofx":
                            artifacts[path.name] = normalize_ofx(path.read_text(encoding="utf-8"), empty=kind == "empty")
                    results[f"{key}/{label}"] = {"snapshot": {"response": response,
                        "metadata": json.loads(metadata.read_text(encoding="utf-8")), "artifacts": artifacts}}

                def run(label, callback, *, error=None):
                    clock[0] += timedelta(seconds=1)
                    before = metadata.read_bytes()
                    try:
                        response = callback(storage())
                    except (ValueError, AnalysisEditConflictError, AnalysisNotFoundError, InvalidFileContentError) as exc:
                        if error is None or not isinstance(exc, error):
                            raise
                        assert metadata.read_bytes() == before, f"{key}/{label}: error changed persisted state"
                        response = {"error": type(exc).__name__, "message": str(exc)}
                    else:
                        assert error is None, f"{key}/{label}: expected rejection"
                    capture(label, response)
                    return response

                capture("initial", None)
                if kind == "specialized":
                    run("reject-edit", lambda s: s.apply_convert_edits(data.analysis_id, []), error=InvalidFileContentError)
                    continue
                if kind == "empty":
                    run("settings", lambda s: s.apply_convert_edits(data.analysis_id, [], bank_code="341", opening_balance=150))
                    run("download", lambda s: s.get_convert_report_path(data.analysis_id, "ofx", closing_balance=150).name)
                    continue
                first = data.preview_transactions[0].transaction_id or "row_1"
                update = {"row_id": first, "date": "2026-04-05", "description": "Correção R$ café", "debit": 2.75}
                if kind == "zero":
                    update.pop("debit")
                saved = run("update", lambda s: s.apply_convert_edits(data.analysis_id, [update], operation_id="edit-1"))
                assert saved["preview_transactions"][0]["amount"] == (0 if kind == "zero" else -2.75)
                run("replay", lambda s: s.apply_convert_edits(data.analysis_id, [update], operation_id="edit-1"))
                run("confirm", lambda s: s.get_convert_edit_operation(data.analysis_id, "edit-1"))
                run("changed-retry", lambda s: s.apply_convert_edits(data.analysis_id, [{**update, "debit": 3}],
                                                                    operation_id="edit-1"), error=AnalysisEditConflictError)
                run("stale-version", lambda s: s.apply_convert_edits(data.analysis_id, [update], expected_updated_at="old"),
                    error=AnalysisEditConflictError)
                for action in ("delete", "restore"):
                    run(action, lambda s, action=action: s.apply_convert_edits(data.analysis_id, [{**update, "action": action}]))
                run("superseded-receipt", lambda s: s.get_convert_edit_operation(data.analysis_id, "edit-1"),
                    error=AnalysisEditConflictError)
                box = {"page": 1, "left": 0.1, "top": 0.3, "width": 0.6, "height": 0.03}
                for label, boxes in (("annotate", [box]), ("clear-annotation", [])):
                    run(label, lambda s, boxes=boxes: s.apply_convert_edits(data.analysis_id,
                        [{"action": "annotate", "row_id": first, "review_regions": boxes}], operation_id=label))
                run("invalid-batch", lambda s: s.apply_convert_edits(data.analysis_id,
                    [update, {"action": "unknown", "row_id": first}]), error=ValueError)
                insertion = {"action": "insert", "transaction_id": "manual-fixed", "anchor_transaction_id": first,
                             "insert_position": 1, "date": "2026-04-06", "description": "Manual", "credit": 12.34}
                run("insert", lambda s: s.apply_convert_edits(data.analysis_id, [insertion], operation_id="insert-1"))
                run("insert-retry", lambda s: s.apply_convert_edits(data.analysis_id, [insertion], operation_id="insert-1"))
                if kind == "multiconta":
                    run("reject-global-settings", lambda s: s.apply_convert_edits(data.analysis_id, [], opening_balance=100),
                        error=InvalidFileContentError)
                    run("reject-download-settings", lambda s: s.get_convert_report_path(data.analysis_id, "ofx", bank_code="341"),
                        error=InvalidFileContentError)
                else:
                    run("settings", lambda s: s.apply_convert_edits(data.analysis_id, [], opening_balance=100,
                                                                   closing_balance=250, bank_branch="1234", account_number="999"))
                    run("download", lambda s: s.get_convert_report_path(data.analysis_id, "ofx", bank_code="341").name)
    report.write_text(json.dumps({"cases": results}, ensure_ascii=True), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-ref")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "tmp/ofx68-review-gate")
    parser.add_argument("--worker", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--approvals", type=Path, help="Exact authorized differences bound to the base revision and candidate app tree.")
    args = parser.parse_args()
    os.environ.update(TEXTRACT_ENABLED="false", PDF_OCR_ENABLED="false", AI_RECOVERY_MODE="off",
                      PYTHONDONTWRITEBYTECODE="1", CANONICAL_LAYOUT_CAPTURE_ENABLED="false", PDF_MULTI_ACCOUNT_ENABLED="true",
                      PYTHONHASHSEED="0")
    if args.worker:
        worker(args.worker, args.report)
        return 0
    if not args.base_ref:
        parser.error("--base-ref is required")
    ref = subprocess.check_output(["git", "rev-parse", "--verify", "--end-of-options", args.base_ref + "^{commit}"],
                                  cwd=ROOT, text=True).strip()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    candidate_app_tree = subprocess.check_output(["git", "rev-parse", "HEAD:backend/app"], cwd=ROOT, text=True).strip()
    approvals = json.loads(args.approvals.read_text(encoding="utf-8")) if args.approvals else []
    if approvals and subprocess.check_output(["git", "status", "--porcelain", "--", "backend/app"], cwd=ROOT, text=True).strip():
        parser.error("Approvals require committed application code so the candidate tree is exact")
    with tempfile.TemporaryDirectory(prefix="gettdone-review-baseline-") as temporary:
        archive = subprocess.check_output(["git", "archive", "--format=tar", ref, "backend/app"], cwd=ROOT)
        with tarfile.open(fileobj=BytesIO(archive)) as tar:
            tar.extractall(temporary, filter="data")
        candidate = Path(temporary) / "candidate/backend"
        copytree(ROOT / "backend/app", candidate / "app", ignore=ignore_patterns("__pycache__", "*.pyc"))
        outputs = []
        for name, backend in (("baseline", Path(temporary) / "backend"), ("candidate", candidate)):
            report = args.output_dir.resolve() / f"{name}.json"
            try:
                subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker", str(backend), "--report", str(report)],
                               check=True, cwd=ROOT, timeout=180)
            except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
                failure = {"baseline_commit": ref, "errors": [f"{name}: worker infrastructure failure ({type(exc).__name__})"]}
                (args.output_dir / "comparison.json").write_text(json.dumps(failure, indent=2), encoding="utf-8")
                print(json.dumps(failure))
                return 1
            outputs.append(json.loads(report.read_text(encoding="utf-8"))["cases"])
    candidate_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    source_hash = hashlib.sha256()
    for path in sorted((ROOT / "backend/app").rglob("*.py")):
        source_hash.update(str(path.relative_to(ROOT)).encode() + path.read_bytes())
    summary = {"baseline_commit": ref, "candidate_commit": candidate_commit,
               "candidate_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip()),
               "candidate_app_sha256": source_hash.hexdigest(),
               "candidate_app_tree": candidate_app_tree,
               **compare_snapshots(*outputs, approvals=approvals, reference_commit=ref, candidate_app_tree=candidate_app_tree)}
    (args.output_dir / "comparison.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k not in {"differences", "platform_geometry_differences"}}))
    return int(bool(summary["errors"]))


if __name__ == "__main__":
    raise SystemExit(main())
