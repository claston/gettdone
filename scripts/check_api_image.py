"""Synthetic runtime checks, mounted read-only into the built image by CI.

Only the Textract network boundary is replaced; parsing, rendering, exports
and error propagation use the production implementation. Never calls AWS.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from importlib import util
from io import BytesIO
from pathlib import Path
from unittest.mock import patch


def check_textract_runtime() -> list[str]:
    import pypdfium2
    from app.application import pdf_parser
    from app.application.default_conversion_pipeline import build_default_conversion_pipeline
    from app.application.errors import InvalidFileContentError
    from app.application.pdf_ocr import is_pdf_ocr_enabled
    from app.workers.worker_image_canary import _pdf_fixture
    from pypdf import PdfReader

    if is_pdf_ocr_enabled():
        raise RuntimeError("Production images must disable local OCR by default.")
    raw = _pdf_fixture()
    # Pillow was previously pulled in by pytesseract. Exercise it explicitly.
    with pypdfium2.PdfDocument(raw) as document:
        page = document[0]
        bitmap = page.render(scale=0.5)
        try:
            with bitmap.to_pil() as image:
                output = BytesIO()
                image.save(output, format="PNG")
                if not output.getvalue().startswith(b"\x89PNG\r\n\x1a\n"):
                    raise RuntimeError("PDF rendering did not produce a PNG.")
        finally:
            bitmap.close()
            page.close()

    lines = PdfReader(BytesIO(raw)).pages[0].extract_text().splitlines()
    blocks = [{"Id": f"line-{index}", "BlockType": "LINE", "Page": 1, "Text": line,
               "Confidence": 99, "Geometry": {"BoundingBox": {
                   "Left": 0.08, "Top": 0.02 + index * 0.04, "Width": 0.85, "Height": 0.02,
               }}} for index, line in enumerate(lines)]
    response = {"document_hash": "synthetic-image-check", "page_count": 1, "blocks": blocks,
                "metrics": {"textract_mode": "text"}}
    with patch.dict(os.environ, TEXTRACT_ENABLED="true", TEXTRACT_FORCE="true", TEXTRACT_TEMP_BUCKET="synthetic-unused"):
        with patch.object(pdf_parser.TextractGateway, "analyze_pdf", return_value=response) as gateway:
            analysis = build_default_conversion_pipeline().run(
                filename="synthetic-textract.pdf", raw_bytes=raw, analysis_id="an_imagetextract",
            ).analysis_data
            gateway.assert_called_once()
            if sorted(row.amount for row in analysis.report_transactions) != [-40, 40]:
                raise RuntimeError("Textract conversion changed the synthetic amounts.")
            if any(row.source_page != 1 for row in analysis.report_transactions):
                raise RuntimeError("Textract conversion lost source pages.")
            if analysis.pdf_processing_metrics.get("extraction_provider") != "aws_textract":
                raise RuntimeError("Synthetic OCR did not use the Textract path.")
        error = InvalidFileContentError("Synthetic Textract provider failure")
        with patch.object(pdf_parser.TextractGateway, "analyze_pdf", side_effect=error), patch.object(
            pdf_parser, "_parse_scanned_pdf_with_local_ocr_adapter",
            side_effect=AssertionError("Textract failure must not invoke local OCR"),
        ):
            try:
                pdf_parser.parse_pdf_transactions(raw)
            except InvalidFileContentError as caught:
                if caught is not error:
                    raise RuntimeError("The original Textract error was replaced.") from caught
            else:
                raise RuntimeError("Textract failure was silently accepted.")
    return ["pillow_png", "textract_conversion", "textract_failure_without_local_fallback"]


def check_api_image() -> list[str]:
    for module in ("pytest", "xdist", "ruff", "pytesseract", "paddleocr"):
        if util.find_spec(module) is not None:
            raise RuntimeError(f"Unexpected production dependency: {module}")
    for executable in ("tesseract", "gcc", "g++", "make"):
        if shutil.which(executable):
            raise RuntimeError(f"Unexpected production executable: {executable}")
    for relative in ("tests", "synthetic_pdf_corpus", "venv", "tmp/tessdata"):
        if (Path.cwd() / relative).exists():
            raise RuntimeError(f"Unexpected production directory: {relative}")
    for relative in ("alembic.ini", "alembic/env.py", "scripts/run_alembic_migrations.py", "../frontend/index.html"):
        if not (Path.cwd() / relative).is_file():
            raise RuntimeError(f"Missing runtime asset: {relative}")

    import sqlite3

    import alembic  # noqa: F401
    from app.main import app
    from app.workers.worker_image_canary import run_checks

    with sqlite3.connect(":memory:") as connection:
        if connection.execute("select 40 - 40").fetchone() != (0,):
            raise RuntimeError("API SQLite runtime is unavailable.")
    if not any(getattr(route, "path", None) == "/convert" for route in app.routes):
        raise RuntimeError("Conversion route was not loaded.")
    with patch.dict(os.environ, TEXTRACT_ENABLED="false", TEXTRACT_FORCE="false"):
        native = run_checks()
    return ["runtime_inventory", "api_import", "sqlite", "migration_assets", *native["checks"], *check_textract_runtime()]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--textract-only", action="store_true", help="Also validates the worker's Textract-only runtime")
    args = parser.parse_args()
    # The file is mounted outside the application, and is never copied into it.
    sys.path.insert(0, str(Path.cwd()))
    checks = check_textract_runtime() if args.textract_only else check_api_image()
    print(json.dumps({"status": "passed", "checks": checks}))
