import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _run_in_fresh_process(source: str) -> None:
    result = subprocess.run(
        [sys.executable, "-c", source],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    "module",
    [
        "app.application.pdf_parser",
        "app.application.parsers",
        "app.application.parsers.service",
        "app.application.parsers.bank_statement",
        "app.application.conversion",
        "app.application.conversion.uploaded_document",
        "app.application.conversion.document_extractor",
        "app.application.conversion.document_conversion_pipeline",
        "app.application.conversion_pipeline",
        "app.workers.conversion_lambda",
    ],
)
def test_public_module_can_be_imported_first(module: str) -> None:
    _run_in_fresh_process(f"import importlib; importlib.import_module({module!r})")


@pytest.mark.parametrize(
    "module",
    ["app.application.parsers.csv", "app.application.conversion.identity"],
)
def test_leaf_import_does_not_initialize_unrelated_services(module: str) -> None:
    _run_in_fresh_process(
        f"import importlib, sys; importlib.import_module({module!r}); "
        "forbidden = {'app.application.pdf_parser', "
        "'app.application.conversion.document_conversion_pipeline', "
        "'app.application.conversion.postgres_conversion_batch_repository', "
        "'app.application.conversion.sqs_conversion_queue'}; "
        "assert not forbidden.intersection(sys.modules), forbidden.intersection(sys.modules)"
    )


def test_package_exports_keep_the_existing_class_identity() -> None:
    _run_in_fresh_process(
        "from app.application.parsers import ParsingService; "
        "from app.application.parsers.service import ParsingService as ConcreteParser; "
        "from app.application.conversion import ConversionJob, UploadedDocument; "
        "from app.application.conversion.conversion_job import ConversionJob as ConcreteJob; "
        "from app.application.conversion.uploaded_document import UploadedDocument as ConcreteDocument; "
        "assert ParsingService is ConcreteParser; "
        "assert ConversionJob is ConcreteJob; "
        "assert UploadedDocument is ConcreteDocument"
    )
