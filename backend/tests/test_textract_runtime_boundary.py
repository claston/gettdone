"""Production OCR remains functional when local OCR packages cannot be imported."""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.architecture


def test_textract_conversion_rendering_and_failure_without_local_ocr():
    script = r'''
import importlib.abc
import json
import os
import runpy
import sys

class NoLocalOCR(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in {"pytesseract", "paddleocr"}:
            raise AssertionError("Production must not import local OCR: " + fullname)

sys.meta_path.insert(0, NoLocalOCR())
sys.path.insert(0, os.getcwd())
os.environ.update(PDF_OCR_ENABLED="false", AWS_EC2_METADATA_DISABLED="true")
probe = runpy.run_path(sys.argv[1])
print(json.dumps(probe["check_textract_runtime"]()))
'''
    result = subprocess.run([sys.executable, "-c", script, str(ROOT / "scripts/check_api_image.py")],
                            cwd=ROOT / "backend", capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "textract_failure_without_local_fallback" in result.stdout
