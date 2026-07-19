from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.application.pdf_parser import parse_pdf_transactions  # noqa: E402
from synthetic_pdf_corpus.catalog import load_scenarios  # noqa: E402
from synthetic_pdf_corpus.generator import generate_pdf  # noqa: E402
from synthetic_pdf_corpus.runner import CorpusRunResult, evaluate_pdf, summarize_results  # noqa: E402

_DEFAULT_SCENARIOS_DIR = _BACKEND_DIR / "tests" / "fixtures" / "pdf_scenarios"
_DEFAULT_OUTPUT_DIR = _BACKEND_DIR / "tmp" / "synthetic_pdf_corpus"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate deterministic synthetic statement PDFs and optionally evaluate the real parser pipeline."
    )
    parser.add_argument("--scenarios-dir", default=str(_DEFAULT_SCENARIOS_DIR))
    parser.add_argument("--output-dir", default=str(_DEFAULT_OUTPUT_DIR))
    parser.add_argument("--scenario", action="append", default=[], help="Scenario id filter. Can be repeated.")
    parser.add_argument("--variants", default="native_text,scanned", help="Comma-separated variants.")
    parser.add_argument("--evaluate", action="store_true", help="Run generated PDFs through parse_pdf_transactions.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    scenarios = load_scenarios(Path(args.scenarios_dir))
    selected_ids = {str(item).strip() for item in args.scenario if str(item).strip()}
    if selected_ids:
        scenarios = tuple(scenario for scenario in scenarios if scenario.scenario_id in selected_ids)
    if not scenarios:
        print("ERROR no synthetic PDF scenarios selected")
        return 1

    variants = tuple(item.strip() for item in str(args.variants).split(",") if item.strip())
    if not variants:
        print("ERROR no synthetic PDF variants selected")
        return 1

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    results: list[CorpusRunResult] = []
    generated_count = 0

    for scenario in scenarios:
        for variant in variants:
            if variant not in scenario.variants:
                continue
            raw_pdf = generate_pdf(scenario, variant=variant)
            output_path = output_dir / f"{scenario.scenario_id}__{variant}.pdf"
            output_path.write_bytes(raw_pdf)
            generated_count += 1
            print(f"GENERATED scenario={scenario.scenario_id} variant={variant} path={output_path}")
            if args.evaluate:
                result = evaluate_pdf(
                    scenario,
                    variant=variant,
                    raw_pdf=raw_pdf,
                    parse_pdf=parse_pdf_transactions,
                )
                results.append(result)
                _print_evaluation(result)

    if args.evaluate:
        summary = summarize_results(results)
        report_path = output_dir / "evaluation.json"
        report_path.write_text(
            json.dumps(
                {
                    "summary": asdict(summary),
                    "results": [_serialize_result(result) for result in results],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"REPORT path={report_path}")
        print(
            f"SUMMARY generated={generated_count} evaluated={summary.evaluated_count} "
            f"passed={summary.passed_count} known_gaps={summary.known_gap_count} "
            f"blocking_failures={summary.blocking_failure_count} "
            f"expected_tx={summary.expected_transaction_count} matched_tx={summary.matched_transaction_count} "
            f"false_positives={summary.false_positive_count} recall={summary.recall:.3f} "
            f"precision={summary.precision:.3f}"
        )
    else:
        print(f"SUMMARY generated={generated_count} evaluated=0 blocking_failures=0")
    return 2 if any(result.blocks_ci for result in results) else 0


def _print_evaluation(result: CorpusRunResult) -> None:
    if result.success:
        status = "PASS"
    elif result.blocks_ci:
        status = "FAIL"
    else:
        status = "KNOWN_GAP"
    summary = result.evaluation.summary if result.evaluation is not None else result.error
    print(
        f"{status} scenario={result.scenario_id} variant={result.variant} "
        f"parser={result.selected_parser} reason={result.parser_selection_reason} {summary}"
    )


def _serialize_result(result: CorpusRunResult) -> dict[str, object]:
    evaluation = result.evaluation
    return {
        "scenario_id": result.scenario_id,
        "variant": result.variant,
        "success": result.success,
        "blocks_ci": result.blocks_ci,
        "selected_parser": result.selected_parser,
        "parser_selection_reason": result.parser_selection_reason,
        "expected_parser": result.expected_parser,
        "parser_matches_expectation": result.parser_matches_expectation,
        "error": result.error,
        "known_gap": result.known_gap,
        "evaluation": (
            {
                **asdict(evaluation),
                "unexpected_transactions": [
                    {
                        "date": str(getattr(item, "date", "")),
                        "description": str(getattr(item, "description", "")),
                        "amount": float(getattr(item, "amount", 0.0)),
                        "type": str(getattr(item, "type", "")),
                    }
                    for item in evaluation.unexpected_transactions
                ],
            }
            if evaluation is not None
            else None
        ),
    }


if __name__ == "__main__":
    raise SystemExit(main())
