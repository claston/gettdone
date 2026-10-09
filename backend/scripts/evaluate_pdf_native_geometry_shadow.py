from __future__ import annotations

import argparse
import hashlib
import os
from collections import Counter
from pathlib import Path
from statistics import median

from app.application.normalization.pdf_native_geometry_shadow import run_native_geometry_shadow
from app.application.pdf_parser import (
    _parse_pdf_transactions_baseline,
    _parse_pdf_transactions_from_page_texts,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare the current PDF parser with native geometric normalization without changing output."
    )
    parser.add_argument("--samples-dir", required=True, help="Private directory containing PDF samples.")
    parser.add_argument("--glob", default="*.pdf", help="Glob used to select samples recursively.")
    parser.add_argument("--max-files", type=int, default=0, help="Limit selected files (0 = no limit).")
    parser.add_argument(
        "--allow-baseline-ocr",
        action="store_true",
        help="Keep OCR enabled for the official baseline if the environment enables it.",
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    samples_dir = Path(args.samples_dir).resolve()
    if not samples_dir.is_dir():
        print("ERROR samples directory not found")
        return 1
    if not args.allow_baseline_ocr:
        os.environ["PDF_OCR_ENABLED"] = "false"

    samples = sorted(samples_dir.rglob(args.glob))
    if args.max_files > 0:
        samples = samples[: args.max_files]
    if not samples:
        print("ERROR no PDF files matched")
        return 1

    observations = []
    for sample in samples:
        raw_bytes = sample.read_bytes()
        sample_id = hashlib.sha256(raw_bytes).hexdigest()[:12]
        baseline_result = None
        baseline_error = None
        try:
            baseline_result = _parse_pdf_transactions_baseline(raw_bytes)
        except Exception as exc:  # private evaluation must continue through malformed samples
            baseline_error = exc
        observation = run_native_geometry_shadow(
            raw_bytes,
            baseline_result=baseline_result,
            baseline_error=baseline_error,
            parse_page_texts=_parse_pdf_transactions_from_page_texts,
        )
        observations.append(observation)
        print(
            " ".join(
                (
                    f"sample={sample_id}",
                    f"classification={observation.classification}",
                    f"baseline={observation.baseline_status}:{observation.baseline_transactions}",
                    f"geometry={observation.geometry_status}:{observation.geometry_transactions}",
                    f"matched={observation.matched_transactions}",
                    f"conflicts={observation.date_conflicts + observation.amount_conflicts + observation.sign_conflicts}",
                    f"duration_ms={observation.duration_ms}",
                )
            )
        )

    counts = Counter(item.classification for item in observations)
    durations = sorted(item.duration_ms for item in observations)
    p95_index = max(0, min(len(durations) - 1, int(len(durations) * 0.95 + 0.999999) - 1))
    print("Native geometry shadow summary")
    print(f"samples={len(observations)}")
    print("classifications=" + ",".join(f"{key}:{counts[key]}" for key in sorted(counts)))
    print(f"latency_median_ms={int(median(durations))}")
    print(f"latency_p95_ms={durations[p95_index]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
