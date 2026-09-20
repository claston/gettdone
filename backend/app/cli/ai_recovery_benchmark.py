from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import replace
from io import BytesIO
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pypdf import PdfReader

from app.adapters.ai_recovery.bedrock_nova import build_bedrock_nova_extractor
from app.application.ai_recovery.benchmark import AIRecoveryBenchmarkCase, run_ai_recovery_benchmark
from app.application.ai_recovery.config import AIRecoveryConfig
from app.application.ai_recovery.models import AIStatement

_SAFE_CASE_ID = re.compile(r"[A-Za-z0-9_.-]+")


class _ManifestCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=100)
    pdf: str = Field(min_length=1, max_length=500)
    page_count: int | None = Field(default=None, ge=1, le=15)
    expected: AIStatement


class _Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cases: list[_ManifestCase] = Field(min_length=1, max_length=50)


def load_benchmark_cases(manifest_path: Path) -> list[AIRecoveryBenchmarkCase]:
    manifest_path = manifest_path.resolve()
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest = _Manifest.model_validate(payload)
    except (OSError, ValueError, ValidationError) as exc:
        raise ValueError("Invalid benchmark manifest.") from exc

    base_directory = manifest_path.parent
    seen_ids: set[str] = set()
    cases: list[AIRecoveryBenchmarkCase] = []
    for item in manifest.cases:
        if _SAFE_CASE_ID.fullmatch(item.id) is None or item.id in seen_ids:
            raise ValueError("Benchmark case identifiers must be unique and machine-readable.")
        seen_ids.add(item.id)

        pdf_path = (base_directory / item.pdf).resolve()
        if not pdf_path.is_relative_to(base_directory) or pdf_path.suffix.lower() != ".pdf":
            raise ValueError("Benchmark PDF paths must remain inside the manifest directory.")
        try:
            raw_bytes = pdf_path.read_bytes()
            page_count = len(PdfReader(BytesIO(raw_bytes)).pages)
        except Exception as exc:
            raise ValueError("Benchmark case contains an unreadable PDF.") from exc
        if not 1 <= page_count <= 15 or (item.page_count is not None and item.page_count != page_count):
            raise ValueError("Benchmark PDF page count is invalid.")
        cases.append(
            AIRecoveryBenchmarkCase(
                case_id=item.id,
                filename=pdf_path.name,
                raw_bytes=raw_bytes,
                page_count=page_count,
                expected=item.expected,
            )
        )
    return cases


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Benchmark AI recovery against a private labeled PDF manifest.")
    parser.add_argument("--manifest", required=True, help="Private JSON manifest; PDF paths must be relative to it.")
    parser.add_argument("--output", help="Optional path for the aggregate JSON report.")
    parser.add_argument("--model-id", help="Override AI_RECOVERY_MODEL_ID.")
    parser.add_argument("--region", help="Override AI_RECOVERY_AWS_REGION.")
    parser.add_argument("--allow-bedrock-call", action="store_true", help="Acknowledge that this command makes paid AWS calls.")
    parser.add_argument("--input-cost-per-million-usd", type=float)
    parser.add_argument("--output-cost-per-million-usd", type=float)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if not args.allow_bedrock_call:
        print("ERROR --allow-bedrock-call is required because this benchmark makes paid AWS calls.", file=sys.stderr)
        return 2

    try:
        _validate_cost_rates(args.input_cost_per_million_usd, args.output_cost_per_million_usd)
        cases = load_benchmark_cases(Path(args.manifest))
        config = AIRecoveryConfig.from_mapping(os.environ)
        if args.model_id:
            config = replace(config, model_id=args.model_id.strip())
        if args.region:
            config = replace(config, region_name=args.region.strip())
        extractor = build_bedrock_nova_extractor(config)
        report = run_ai_recovery_benchmark(cases=cases, extractor=extractor)
    except Exception:
        print("ERROR benchmark could not be completed. No document content was logged.", file=sys.stderr)
        return 2

    payload = report.to_dict()
    estimated_cost = _estimate_cost(
        input_tokens=report.input_tokens,
        output_tokens=report.output_tokens,
        input_rate=args.input_cost_per_million_usd,
        output_rate=args.output_cost_per_million_usd,
    )
    if estimated_cost is not None:
        payload["estimated_cost_usd"] = estimated_cost
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(serialized + "\n", encoding="utf-8")
    else:
        print(serialized)
    return 3 if report.critical_false_accepts > 0 or report.extraction_failures > 0 else 0


def _estimate_cost(
    *,
    input_tokens: int,
    output_tokens: int,
    input_rate: float | None,
    output_rate: float | None,
) -> float | None:
    if input_rate is None or output_rate is None:
        return None
    total = input_tokens * input_rate / 1_000_000 + output_tokens * output_rate / 1_000_000
    return round(total, 6)


def _validate_cost_rates(input_rate: float | None, output_rate: float | None) -> None:
    if (input_rate is None) != (output_rate is None):
        raise ValueError("Both benchmark token rates must be provided together.")
    if input_rate is not None and (input_rate < 0 or output_rate is None or output_rate < 0):
        raise ValueError("Benchmark token rates must not be negative.")


if __name__ == "__main__":
    raise SystemExit(main())
