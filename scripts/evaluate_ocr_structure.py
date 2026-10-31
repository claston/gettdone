"""Offline comparison of two saved OCR responses; never calls AWS or changes runtime.

References and financial output belong in ignored local/ or tmp/, not in Git.
The schema contains vocabulary/column roles, not document-specific coordinates.
"""

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.application import pdf_parser  # noqa: E402
from app.application.ocr_financial_interpretation import (  # noqa: E402
    CashTablePolicy,
    interpret_cash_table,
)  # noqa: E402
from app.application.ocr_structure import (  # noqa: E402
    OcrColumn,
    OcrTableSchema,
    reconstruct_ocr_table,
)  # noqa: E402
from app.application.textract_extraction_mapper import map_textract_blocks_to_extraction  # noqa: E402
from app.application.textract_transaction_adapter import (  # noqa: E402
    adapt_textract_extraction_to_transactions,
)


def read_json(path):
    return path, json.loads(path.read_text(encoding="utf-8"))


def run(args):
    output = args.output.resolve()
    if not any(output.is_relative_to(ROOT / name) for name in ("local", "tmp")):
        raise ValueError(
            "Private evaluation output must stay under ignored local/ or tmp/"
        )
    output.mkdir(parents=True, exist_ok=True)
    _, config = read_json(args.schema)
    schema = OcrTableSchema(
        tuple(
            OcrColumn(c["key"], tuple(c["headers"]), c.get("anchor_pattern"))
            for c in config["columns"]
        )
    )
    policy_config = config["financial_policy"]
    policy = CashTablePolicy(
        tuple(policy_config["opening_labels"]),
        tuple(policy_config["closing_labels"]),
        minimum_critical_confidence=policy_config.get(
            "minimum_critical_confidence", 0.90
        ),
    )
    with args.reference.open(encoding="utf-8-sig", newline="") as stream:
        reference = [
            row
            for row in csv.DictReader(stream, delimiter=";")
            if row["file"] == args.reference_filename
        ]
    if not reference:
        raise ValueError("No reference rows for the selected document")
    expected = Counter(
        (
            datetime.strptime(row["date"], "%d/%m/%Y").date().isoformat(),
            Decimal(row["amount"]),
            int(row["page"]),
        )
        for row in reference
    )
    document_hash = hashlib.sha256(args.source.read_bytes()).hexdigest()
    summaries, results = [], {}
    for mode, path in (("text", args.text_result), ("analysis", args.analysis_result)):
        _, raw = read_json(path)
        extraction = map_textract_blocks_to_extraction(
            document_hash=document_hash,
            blocks=raw["Blocks"],
            page_count=raw["DocumentMetadata"]["Pages"],
        )
        started = perf_counter()
        baseline = (
            pdf_parser._parse_pdf_transactions_from_page_texts(
                [
                    "\n".join(line.text for line in page.lines)
                    for page in extraction.pages
                ]
            )
            if mode == "text"
            else adapt_textract_extraction_to_transactions(extraction)
        )
        baseline_rows = baseline.canonical_transactions or []
        baseline_actual = Counter(
            (row.date, Decimal(str(row.amount)), row.source_page)
            for row in baseline_rows
        )
        summaries.append(
            {
                "reader": mode,
                "interpreter": "legacy_without_shared_ocr",
                "count": len(baseline_rows),
                "net": str(
                    sum((Decimal(str(row.amount)) for row in baseline_rows), Decimal(0))
                ),
                "exact_date_amount_page_multiset": baseline_actual == expected,
                "missing_reference_rows": sum((expected - baseline_actual).values()),
                "unexpected_rows": sum((baseline_actual - expected).values()),
                "financial_controls_validated": bool(
                    baseline.parse_metrics.get("financial_controls_validated")
                ),
                "seconds": round(perf_counter() - started, 4),
            }
        )
        started = perf_counter()
        structure = reconstruct_ocr_table(extraction, schema)
        result = interpret_cash_table(extraction, structure, policy)
        results[mode] = result
        actual = Counter(
            (row.date, row.amount, row.source_page) for row in result.movements
        )
        summary = {
            "reader": mode,
            "interpreter": "shared_experiment",
            "count": len(result.movements),
            "net": str(sum((row.amount for row in result.movements), Decimal(0))),
            "exact_date_amount_page_multiset": actual == expected,
            "missing_reference_rows": sum((expected - actual).values()),
            "unexpected_rows": sum((actual - expected).values()),
            "expected_row_anchors": structure.expected_rows,
            "page_counts": dict(Counter(row.source_page for row in result.movements)),
            "opening": str(result.opening),
            "printed_closing": str(result.closing),
            "balance_matches": result.balance_matches,
            "requires_review": result.requires_review,
            "issues": dict(Counter(issue.code for issue in result.issues)),
            "evidence_kinds": dict(
                Counter(row.evidence_kind for row in structure.rows)
            ),
            "seconds": round(perf_counter() - started, 4),
        }
        summaries.append(summary)
        (output / f"{mode}-shared.json").write_text(
            json.dumps(
                {
                    "summary": summary,
                    "structure": asdict(structure),
                    "result": asdict(result),
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
    text_rows, analysis_rows = results["text"].movements, results["analysis"].movements
    descriptions_equal = len(text_rows) == len(analysis_rows) and all(
        (a.date, a.amount, a.source_page, " ".join(a.description.split()))
        == (b.date, b.amount, b.source_page, " ".join(b.description.split()))
        for a, b in zip(text_rows, analysis_rows)
    )
    report = {
        "document_sha256": document_hash,
        "physical_pages": raw["DocumentMetadata"]["Pages"],
        "reference_count": len(reference),
        "summaries": summaries,
        "word_vs_cell_descriptions_equal": descriptions_equal,
        "limitations": [
            "one real document; no general accuracy claim",
            "confidence scores from words and cells are not calibrated equivalents",
            "matching balances do not certify every description/account association",
            "experimental single-block signed-cash semantics",
        ],
    }
    (output / "comparison.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--text-result", type=Path, required=True)
    parser.add_argument("--analysis-result", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--reference-filename", required=True)
    parser.add_argument(
        "--schema",
        type=Path,
        default=ROOT / "scripts/fixtures/ocr_signed_cash_schema.json",
    )
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
