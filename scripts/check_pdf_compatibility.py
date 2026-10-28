"""Offline PDF release gate: product contracts plus comparison with the PR base.

Only fictional inputs are generated. The baseline is extracted with git archive;
no checkout/worktree, external OCR, AWS credentials or production data is used.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from collections import Counter
from decimal import Decimal
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def movement_key(rows):
    return Counter((str(day), format(Decimal(str(amount)), ".2f")) for day, amount in rows)


def compare_reports(cases, baseline, candidate):
    """Expected source truth is independent of both parser implementations."""
    errors, changes = [], []
    for case in cases:
        key, expected = case["id"], case["expected"]
        old, new = baseline[key], candidate[key]
        if expected.get("rejected"):
            if new["success"]:
                errors.append(f"{key}: unreadable/ambiguous input was accepted")
            continue
        if not new["success"]:
            errors.append(f"{key}: readable document rejected ({new.get('error')})")
            continue
        if movement_key(new["movements"]) != movement_key(expected["movements"]):
            errors.append(f"{key}: dates, signs, values or duplicate multiplicity differ from source truth")
        for field in ("document_type", "bank_code", "origin", "formats"):
            if field in expected and new.get(field) != expected[field]:
                errors.append(f"{key}: incorrect {field}: {new.get(field)!r}")
        if expected.get("review") and not new["review"]:
            errors.append(f"{key}: required review warning disappeared")
        if old["success"]:
            changed = (movement_key(old["movements"]) != movement_key(new["movements"])
                       or old.get("document_type") != new.get("document_type")
                       or old.get("bank_code") != new.get("bank_code"))
            if changed:
                reason = case.get("correction_reason")
                if not reason:
                    errors.append(f"{key}: changed a successful baseline without a documented source correction")
                else:
                    changes.append({"case": key, "reason": reason})
            if case.get("review_must_not_regress") and old["review"] and not new["review"]:
                errors.append(f"{key}: baseline review warning was lost")
    return {"errors": errors, "documented_corrections": changes, "cases": len(cases)}


def make_corpus():
    from dataclasses import asdict

    sys.path.insert(0, str(ROOT / "backend"))
    from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf
    from tests.fixtures.recovery_acceptance import bb_outside_header, bradesco_monthly_native, sicredi_combined, xp_investment_pages
    from tests.test_postdeploy_recovery_cases import mercado_related_events
    from tests.test_oct27_bradesco_recovery import bradesco_continuation
    from tests.test_pdf_parser_bradesco_opening_sign import native_pdf, statement_pages
    from tests.test_recovery_case_fixes import mercado_pago_page
    from tests.test_statement_review_policy import SANTANDER
    from tests.test_multi_period_statements import bb_month, pdf
    from tests.test_oct20_new_native_statements import bb_page
    from tests.test_oct27_mercado_grid import mercado_grid
    from tests.test_oct28_remaining_statements import bb_zero_with_blocked, sicoob_descending

    cases = []

    def add(key, pdf, movements, *, ocr=None, **expected):
        cases.append({"id": key, "pdf": base64.b64encode(pdf).decode(),
                      "ocr": asdict(ocr) if ocr else None, "expected": {"movements": movements, **expected}})

    mp = [["2026-07-02", 50], ["2026-07-03", -20], ["2026-07-04", 20]]
    add("bb_pending_deposit_excluded", pdf([bb_zero_with_blocked()]), [["2026-07-01", 50]], bank_code="001", review=True)
    add("bb_prior_day_cash_control", pdf([bb_page().replace("Saldo Anterior", "Saldo do dia")]),
        [["2026-07-02", -194.57], ["2026-07-03", 1100]], bank_code="001")
    displaced = bb_page().replace("02/07/2026 13105 70201 Pagamento de Boleto 194,57 (-)",
        "PAGAMENTO DE BOLETO\n02/07/2026 194,57 (-)")
    add("bb_displaced_history", pdf([displaced]), [["2026-07-02", -194.57], ["2026-07-03", 1100]], bank_code="001")
    add("sicoob_descending_native", sicoob_descending(), [["2026-07-01", 50], ["2026-07-02", -200]], bank_code="756")
    add("bb_unknown_star_is_not_pending_cash", pdf([bb_zero_with_blocked().replace("Depósito bloquead.", "Pix enviado")]),
        [], rejected=True)
    add("mercado_native_row_rules", mercado_grid(), [["2026-10-02", 10], ["2026-10-03", -5]], bank_code="323", review=True)
    add("bb_twelve_month_blocks", pdf([page for month in range(1, 13)
        for page in bb_month(month, opening=1000+(month-1)*180, pages=6)]),
        [[f"2025-{month:02d}-{day:02d}", value] for month in range(1, 13) for day in range(1, 7) for value in (50, -20)],
        bank_code="001")
    add("bb_noncontiguous_blocks", pdf(bb_month(1)+bb_month(3, opening=1030)),
        [["2025-01-01", 50], ["2025-01-01", -20], ["2025-03-01", 50], ["2025-03-01", -20]], bank_code="001", review=True)
    for key, text, review in [("mercado_refund", mercado_pago_page(), False),
                              ("mercado_missing_period", mercado_pago_page().replace("Periodo: De 01-07-2026 al 31-07-2026\n", ""), True),
                              ("mercado_inconsistent_totals", mercado_pago_page().replace("Entradas: R$ 70,00", "Entradas: R$ 71,00"), True),
                              ("mercado_partial", mercado_pago_page().replace("1/1", "1/3"), True)]:
        add(key, generate_native_text_pdf((text.splitlines(),)), mp, bank_code="323", review=review)
    for retained in [False, True]:
        rows = [["2026-07-02", 50], ["2026-07-03", -50], ["2026-07-04", 50]] if retained else [
            ["2026-07-02", 50], ["2026-07-31", 50]]
        add(f"mercado_shared_reference_{retained}", generate_native_text_pdf((mercado_related_events(retained).splitlines(),)),
            rows, bank_code="323", review=True)
    add("santander_partial", generate_native_text_pdf((SANTANDER.splitlines(),)),
        [["2026-09-01", 50], ["2026-09-02", -20]], bank_code="033", review=True)
    add("bradesco_native_wrapped", bradesco_monthly_native(),
        [["2026-12-01", 500], ["2026-12-01", -150], ["2026-12-02", -150]], bank_code="237")
    add("bradesco_missing_period", bradesco_continuation(undated_opening=True, mutation="missing_period"),
        [["2026-12-01", 500], ["2026-12-01", -150], ["2026-12-02", -150]], bank_code="237", review=True)
    add("bradesco_separate_section_opening", native_pdf(statement_pages()),
        [["2026-09-30", 100], ["2026-09-30", -20], ["2026-10-01", -12.5], ["2026-10-01", 20]], bank_code="237")
    add("bb_header_period", bb_outside_header(), [["2026-09-28", -20], ["2026-10-01", -10]], bank_code="001", review=True)
    for mismatch in [False, True]:
        source = sicredi_combined(mismatch=mismatch)
        raw = generate_native_text_pdf((tuple(line.text for line in source.pages[0].lines),))
        add(f"sicredi_combined_{mismatch}", raw,
            [["2026-08-03", -20], ["2026-08-03", -20], ["2026-08-04", 50]], ocr=source,
            bank_code="748", review=mismatch)
        cases[-1]["correction_reason"] = "Restore the printed duplicate and exclude the explicitly future section; previous grouping lost a row."
    source = sicredi_combined(bad_amount=True)
    add("sicredi_unreadable_amount", generate_native_text_pdf((("Synthetic OCR",),)), [], ocr=source, rejected=True)
    add("xp_investment_identity", generate_native_text_pdf(tuple(p.splitlines() for p in xp_investment_pages())),
        [["2026-08-02", 50], ["2026-08-03", -20]], document_type="investment_statement",
        origin="XP Investimentos", bank_code=None, formats=["csv", "xlsx"])
    cases[-1]["correction_reason"] = "Identify the investment report; its two dates and forecasts must not become a Sisprime cash ledger."
    return cases


def worker(backend, corpus, output):
    sys.path.insert(0, str(backend.resolve()))
    from app.application import pdf_parser
    from app.application.default_conversion_pipeline import build_default_conversion_pipeline
    from app.application.document_extraction_models import ExtractedLine, ExtractedPage, ExtractedWord, RawDocumentExtraction

    results = {}
    for case in corpus:
        try:
            raw = base64.b64decode(case["pdf"])
            source = case.get("ocr")
            if source:
                pages = [ExtractedPage(page_number=p["page_number"], lines=[ExtractedLine(**line) for line in p["lines"]],
                    words=[ExtractedWord(**w) for w in p["words"]]) for p in source["pages"]]
                extracted = RawDocumentExtraction(source["provider"], source["document_hash"], pages, {})
                parsed = pdf_parser._parse_scanned_pdf_with_textract_text_pages(extracted)
            else:
                parsed = pdf_parser.parse_pdf_transactions(raw)
            data = build_default_conversion_pipeline().run(filename="fictional.pdf", raw_bytes=raw, analysis_id="an_pdf_gate",
                pdf_parser=lambda *a, **kw: parsed).analysis_data
            doc = data.financial_document
            results[case["id"]] = {"success": True, "movements": [[r.date, r.amount] for r in data.report_transactions],
                "bank_code": data.bank_code, "document_type": data.document_type, "origin": doc and doc.get("origin"),
                "formats": doc and doc["export"]["available_formats"],
                "review": (data.pdf_processing_metrics or {}).get("export_recommendation") == "review_recommended"}
        except Exception as exc:
            results[case["id"]] = {"success": False, "error": type(exc).__name__}
    output.write_text(json.dumps(results, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-ref")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "tmp/pdf-compatibility")
    parser.add_argument("--worker", type=Path)
    parser.add_argument("--corpus", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    os.environ.update(TEXTRACT_ENABLED="false", PDF_OCR_ENABLED="false", AI_RECOVERY_MODE="off",
                      PYTHONDONTWRITEBYTECODE="1", CANONICAL_LAYOUT_CAPTURE_ENABLED="false")
    if args.worker:
        worker(args.worker, json.loads(args.corpus.read_text(encoding="utf-8")), args.report)
        return 0
    if not args.base_ref:
        parser.error("--base-ref is required; an absent baseline must not silently pass")
    # Resolve to a commit hash before passing it to archive (never a shell).
    ref = subprocess.check_output(["git", "rev-parse", "--verify", "--end-of-options", args.base_ref + "^{commit}"], cwd=ROOT, text=True).strip()
    cases = make_corpus()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    corpus = args.output_dir / "corpus.json"
    corpus.write_text(json.dumps(cases), encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="gettdone-pdf-baseline-") as temporary:
        archive = subprocess.check_output(["git", "archive", "--format=tar", ref, "backend/app"], cwd=ROOT)
        with tarfile.open(fileobj=BytesIO(archive)) as tar:
            tar.extractall(temporary, filter="data")
        outputs = []
        for name, backend in [("baseline", Path(temporary) / "backend"), ("candidate", ROOT / "backend")]:
            report = args.output_dir / f"{name}.json"
            subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker", str(backend),
                            "--corpus", str(corpus.resolve()), "--report", str(report.resolve())], check=True, cwd=ROOT, timeout=180)
            outputs.append(json.loads(report.read_text(encoding="utf-8")))
    summary = {"baseline_commit": ref, **compare_reports(cases, *outputs)}
    (args.output_dir / "comparison.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 1 if summary["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
