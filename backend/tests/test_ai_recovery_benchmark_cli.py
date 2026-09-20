import json
import subprocess
import sys
from pathlib import Path

import pytest
from pypdf import PdfWriter

from app.cli.ai_recovery_benchmark import _estimate_cost, _validate_cost_rates, load_benchmark_cases, main

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _expected_statement() -> dict[str, object]:
    return {
        "transactions": [
            {
                "date": "2026-08-02",
                "description": "PIX recebido",
                "amount": "500.00",
                "direction": "credit",
                "running_balance": "1500.00",
                "source_page": 1,
                "source_line": 8,
            }
        ],
        "opening_balance": "1000.00",
        "closing_balance": "1500.00",
        "period_start": "2026-08-01",
        "period_end": "2026-08-31",
        "warnings": [],
    }


def _write_manifest(tmp_path: Path, *, pdf_path: str = "case-001.pdf", case_id: str = "case-001") -> Path:
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    with (tmp_path / "case-001.pdf").open("wb") as pdf_file:
        writer.write(pdf_file)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "id": case_id,
                        "pdf": pdf_path,
                        "page_count": 1,
                        "expected": _expected_statement(),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return manifest


def test_cli_manifest_loader_keeps_expected_data_in_memory_only(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path)

    cases = load_benchmark_cases(manifest)

    assert len(cases) == 1
    assert cases[0].case_id == "case-001"
    assert cases[0].filename == "case-001.pdf"
    assert cases[0].raw_bytes.startswith(b"%PDF")
    assert str(cases[0].expected.transactions[0].amount) == "500.00"


@pytest.mark.parametrize(
    ("pdf_path", "case_id"),
    [
        ("../outside.pdf", "case-001"),
        ("case-001.txt", "case-001"),
        ("case-001.pdf", "nome com cliente"),
    ],
)
def test_cli_manifest_loader_rejects_unsafe_paths_and_identifiers(
    tmp_path: Path,
    pdf_path: str,
    case_id: str,
) -> None:
    manifest = _write_manifest(tmp_path, pdf_path=pdf_path, case_id=case_id)

    with pytest.raises(ValueError):
        load_benchmark_cases(manifest)


def test_cli_requires_explicit_paid_call_acknowledgement(tmp_path: Path, capsys) -> None:
    manifest = _write_manifest(tmp_path)

    exit_code = main(["--manifest", str(manifest)])

    assert exit_code == 2
    output = capsys.readouterr().err
    assert "--allow-bedrock-call" in output


def test_cli_script_can_be_invoked_directly_from_repository_root() -> None:
    result = subprocess.run(
        [sys.executable, str(BACKEND_ROOT / "scripts" / "ai_recovery_benchmark.py"), "--help"],
        cwd=BACKEND_ROOT.parent,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "--allow-bedrock-call" in result.stdout


def test_cli_estimates_cost_only_when_both_current_rates_are_provided() -> None:
    _validate_cost_rates(0.50, 2.00)

    assert _estimate_cost(input_tokens=1_000_000, output_tokens=500_000, input_rate=0.50, output_rate=2.00) == 1.50
    assert _estimate_cost(input_tokens=100, output_tokens=100, input_rate=None, output_rate=None) is None


@pytest.mark.parametrize(("input_rate", "output_rate"), [(1.0, None), (None, 1.0), (-1.0, 1.0), (1.0, -1.0)])
def test_cli_rejects_incomplete_or_negative_cost_rates_before_bedrock_call(
    input_rate: float | None,
    output_rate: float | None,
) -> None:
    with pytest.raises(ValueError):
        _validate_cost_rates(input_rate, output_rate)
