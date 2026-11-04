"""Exercise the Free review banner with the API's document-level review signals."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
SOURCE = Path(__file__).resolve().parents[2] / "frontend" / "ofx-convert.js"


@pytest.mark.skipif(NODE is None, reason="Node is required for frontend regressions")
@pytest.mark.parametrize(
    ("analysis", "expected"),
    [
        ({"pdf_processing_metrics": {"export_recommendation": "review_recommended"}}, "Revise os lançamentos"),
        ({"insights": [{"type": "pdf_export_review_recommended"}]}, "Revise os lançamentos"),
        ({"pdf_processing_metrics": {"canonical_warning_types_list": ["manual_review_recommended"]}},
         "Revise os lançamentos"),
        ({"preview_transactions": [{"warning_types": ["manual_review_recommended"]}]}, "Revise os lançamentos"),
        ({"pdf_processing_metrics": {"canonical_warning_types_list": ["ocr_control_confidence_unconfirmed"]}},
         "Confira os saldos no PDF"),
        ({"pdf_processing_metrics": {"canonical_warning_types_list": ["ocr_structure_review_required"]}},
         "A organização de algumas linhas precisa de conferência"),
        ({"pdf_processing_metrics": {"canonical_warning_types_list": ["ocr_controls_unavailable"]}},
         "Não foi possível identificar os saldos"),
        ({"pdf_processing_metrics": {"statement_no_activity": 1, "export_recommendation": "review_recommended"}},
         "Revise os lançamentos"),
        ({"pdf_processing_metrics": {"canonical_warning_types_list": ["financial_validation_failed"]}},
         "não foi possível confirmar todos os saldos"),
        ({"pdf_processing_metrics": {"export_recommendation": "safe_to_export"}}, None),
        ({"pdf_processing_metrics": {"export_recommendation": "ready"}}, None),
        ({"preview_transactions": [{"warning_types": ["layout_fallback"]}]}, None),
        ({}, None),
    ],
)
def test_free_review_banner_honors_document_review_signals(analysis, expected):
    source = SOURCE.read_text(encoding="utf-8")
    functions = []
    for name in ("isNoActivityStatement", "buildReviewWarningsMarkup", "collectReviewWarnings", "mapWarningTypeExplanation"):
        match = re.search(r"(?m)^  function " + name + r"\(", source)
        assert match is not None, name
        end = source.index("\n  }", match.end()) + len("\n  }")
        functions.append(source[match.start():end])
    program = "\n".join(functions) + "\n" + r"""
const state = { previewRows: [] };
const isPayablesReport = () => false;
const escapeAttr = value => String(value).replace(/&/g, '&amp;').replace(/</g, '&lt;');
process.stdout.write(buildReviewWarningsMarkup(ANALYSIS));
""".replace("ANALYSIS", json.dumps(analysis))
    result = subprocess.run([NODE, "-"], input=program, text=True, encoding="utf-8", capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    if expected is None:
        assert "Atenção na revisão" not in result.stdout
    else:
        assert "Atenção na revisão" in result.stdout
        assert expected in result.stdout
        assert 'aria-live="polite"' in result.stdout
