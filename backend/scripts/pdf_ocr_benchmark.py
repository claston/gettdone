from __future__ import annotations

import argparse
import csv
import os
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

from app.application.pdf_parser import parse_pdf_transactions


@dataclass(frozen=True)
class OcrBenchmarkRow:
    file_name: str
    engine: str
    success: bool
    elapsed_ms: float
    tx_count: int
    parser: str
    reason: str
    error: str


def _run_single(pdf_path: Path, *, engine: str) -> OcrBenchmarkRow:
    previous_engine = os.environ.get("PDF_OCR_ENGINE")
    os.environ["PDF_OCR_ENGINE"] = engine
    started = perf_counter()
    try:
        raw = pdf_path.read_bytes()
        result = parse_pdf_transactions(raw)
        elapsed_ms = round((perf_counter() - started) * 1000, 3)
        return OcrBenchmarkRow(
            file_name=pdf_path.name,
            engine=engine,
            success=True,
            elapsed_ms=elapsed_ms,
            tx_count=len(result.transactions),
            parser=str(result.parse_metrics.get("selected_parser", "")),
            reason=str(result.parse_metrics.get("parser_selection_reason", "")),
            error="",
        )
    except Exception as exc:  # pragma: no cover
        elapsed_ms = round((perf_counter() - started) * 1000, 3)
        return OcrBenchmarkRow(
            file_name=pdf_path.name,
            engine=engine,
            success=False,
            elapsed_ms=elapsed_ms,
            tx_count=0,
            parser="error",
            reason="",
            error=f"{type(exc).__name__}: {exc}",
        )
    finally:
        if previous_engine is None:
            os.environ.pop("PDF_OCR_ENGINE", None)
        else:
            os.environ["PDF_OCR_ENGINE"] = previous_engine


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Benchmark OCR engine choices for PDF parsing.")
    parser.add_argument("--pdf", action="append", default=[], help="Single PDF path. Can be passed multiple times.")
    parser.add_argument("--samples-dir", default="", help="Directory with PDFs for batch benchmark.")
    parser.add_argument("--glob", default="*.pdf", help="Glob used with --samples-dir.")
    parser.add_argument("--engines", default="tesseract,paddle", help="Comma separated list. Example: tesseract,paddle")
    parser.add_argument("--out-csv", default="", help="Optional CSV output path.")
    return parser


def _resolve_files(args: argparse.Namespace) -> list[Path]:
    files: list[Path] = []
    for raw in args.pdf:
        candidate = Path(raw).expanduser()
        if candidate.exists():
            files.append(candidate)
    if args.samples_dir:
        samples_dir = Path(args.samples_dir).expanduser()
        if samples_dir.exists():
            files.extend(sorted(samples_dir.glob(args.glob)))
    unique: dict[str, Path] = {}
    for item in files:
        unique[str(item.resolve())] = item
    return list(unique.values())


def _print_summary(rows: list[OcrBenchmarkRow]) -> None:
    print("OCR Benchmark Results")
    for row in rows:
        status = "OK" if row.success else "ERR"
        line = (
            f"{status} engine={row.engine} file={row.file_name} elapsed_ms={row.elapsed_ms:.3f} "
            f"tx={row.tx_count} parser={row.parser} reason={row.reason}"
        )
        if row.error:
            line += f" error={row.error}"
        print(line)

    print("")
    files = sorted({row.file_name for row in rows})
    for file_name in files:
        by_file = [row for row in rows if row.file_name == file_name]
        if len(by_file) < 2:
            continue
        baseline = by_file[0]
        for competitor in by_file[1:]:
            delta_tx = competitor.tx_count - baseline.tx_count
            delta_ms = round(competitor.elapsed_ms - baseline.elapsed_ms, 3)
            print(
                f"DIFF file={file_name} {baseline.engine}->{competitor.engine} "
                f"delta_tx={delta_tx} delta_ms={delta_ms:+.3f}"
            )


def _write_csv(rows: list[OcrBenchmarkRow], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["file_name", "engine", "success", "elapsed_ms", "tx_count", "parser", "reason", "error"],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "file_name": row.file_name,
                    "engine": row.engine,
                    "success": row.success,
                    "elapsed_ms": row.elapsed_ms,
                    "tx_count": row.tx_count,
                    "parser": row.parser,
                    "reason": row.reason,
                    "error": row.error,
                }
            )


def main() -> int:
    args = _build_parser().parse_args()
    files = _resolve_files(args)
    engines = [part.strip().lower() for part in str(args.engines).split(",") if part.strip()]

    if not files:
        print("ERROR no input PDFs found. Use --pdf or --samples-dir.")
        return 1
    if not engines:
        print("ERROR no engines selected.")
        return 1

    rows: list[OcrBenchmarkRow] = []
    for pdf_path in files:
        for engine in engines:
            rows.append(_run_single(pdf_path, engine=engine))

    _print_summary(rows)

    if args.out_csv:
        _write_csv(rows, Path(args.out_csv).expanduser())
        print(f"\nCSV saved at {Path(args.out_csv).expanduser()}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
