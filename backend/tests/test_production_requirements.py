"""Deployment dependencies must not pull developer tools or local OCR back in."""
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

BACKEND = Path(__file__).resolve().parents[1]


def requirements(filename):
    result = {}
    for line in (BACKEND / filename).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("-r "):
            result.update(requirements(line[3:].strip()))
        else:
            requirement = Requirement(line)
            result[canonicalize_name(requirement.name)] = str(requirement.specifier)
    return result


@pytest.mark.parametrize("filename", ["requirements-runtime.txt", "requirements-worker.txt", "requirements-worker-debian.txt"])
def test_production_dependencies_exclude_developer_tools_and_local_ocr(filename):
    installed = requirements(filename)
    assert not {"pytest", "pytest-xdist", "ruff", "pytesseract", "paddleocr"} & installed.keys()
    # PDF rendering remains necessary for previews and visual PDF recovery.
    assert {"pillow", "pypdfium2", "boto3", "psycopg"} <= installed.keys()


def test_development_install_retains_production_dependencies_and_optional_local_ocr():
    production = requirements("requirements-runtime.txt")
    development = requirements("requirements.txt")
    assert all(development[name] == version for name, version in production.items())
    assert {"pytest", "pytest-xdist", "ruff", "pytesseract"} <= development.keys()
    assert {"httpx", "alembic", "sqlalchemy", "fastapi", "uvicorn"} <= production.keys()
