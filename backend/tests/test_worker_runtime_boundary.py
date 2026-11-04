import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
DOCKERFILE_PATH = ROOT / "Dockerfile.lambda"
DOCKERIGNORE_PATH = ROOT / "Dockerfile.lambda.dockerignore"
WORKER_REQUIREMENTS_PATH = BACKEND_ROOT / "requirements-worker.txt"
WORKER_CONTRACT_PATH = BACKEND_ROOT / "app" / "workers" / "worker_image_contract.py"
pytestmark = pytest.mark.architecture


def _imported_app_modules() -> set[str]:
    command = (
        "import json, sys; "
        "from app.workers.worker_image_contract import verify_worker_imports; "
        "verify_worker_imports(); "
        "print(json.dumps(sorted(name for name in sys.modules if name.startswith('app.'))))"
    )
    result = subprocess.run(
        [sys.executable, "-c", command],
        cwd=BACKEND_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return set(json.loads(result.stdout))


def test_worker_import_does_not_load_web_or_administrative_modules() -> None:
    imported = _imported_app_modules()
    forbidden_prefixes = (
        "app.api",
        "app.main",
        "app.schemas",
        "app.routers",
        "app.application.admin_dashboard_service",
        "app.application.access_control",
        "app.application.checkout_management",
        "app.application.contact_service",
        "app.application.google_oauth_service",
        "app.application.login_tracking",
        "app.application.plan_management",
    )

    unexpected = sorted(
        module
        for module in imported
        if any(module == prefix or module.startswith(f"{prefix}.") for prefix in forbidden_prefixes)
    )

    assert unexpected == []


def test_worker_image_uses_a_dedicated_runtime_contract() -> None:
    dockerfile = DOCKERFILE_PATH.read_text(encoding="utf-8")
    worker_contract = WORKER_CONTRACT_PATH.read_text(encoding="utf-8")

    assert "backend/requirements-worker.txt" in dockerfile
    assert "backend/requirements.txt backend/requirements-lambda.txt" not in dockerfile
    assert "COPY backend/app /var/task/app" not in dockerfile
    assert "COPY backend/app/security_baseline.py /var/task/app/security_baseline.py" not in dockerfile
    assert '"-m", "app.workers.worker_image_contract"' in dockerfile
    assert "from app.security_baseline" not in (BACKEND_ROOT / "app" / "workers" / "conversion_lambda.py").read_text(
        encoding="utf-8"
    )
    assert '"app/application/admin_dashboard_service.py"' in worker_contract
    assert '"app.application.admin_dashboard_service"' in worker_contract


def test_worker_runtime_uses_the_approved_debian_base_and_official_lambda_client() -> None:
    dockerfile = DOCKERFILE_PATH.read_text(encoding="utf-8")
    assert "python:3.12-slim-trixie@sha256:" in dockerfile
    assert "gcr.io/distroless/cc-debian13:nonroot@sha256:" in dockerfile
    assert 'ENTRYPOINT ["/usr/local/bin/python3.12", "-m", "awslambdaric"]' in dockerfile
    assert "COPY --from=build /runtime-status/ /var/lib/dpkg/status.d/" in dockerfile
    assert "dnf " not in dockerfile and "public.ecr.aws/lambda/python" not in dockerfile
    requirements = WORKER_REQUIREMENTS_PATH.read_text(encoding="utf-8")
    assert "pypdf==6.21.4" in requirements
    assert "awslambdaric==4.2.2" in requirements


def test_worker_build_context_excludes_web_and_administrative_sources() -> None:
    dockerignore = DOCKERIGNORE_PATH.read_text(encoding="utf-8").replace("\\", "/")
    excluded = {
        "backend/app/application/admin_dashboard_service.py",
        "backend/app/application/access_control/",
        "backend/app/application/checkout_management.py",
        "backend/app/application/contact_service.py",
        "backend/app/application/google_oauth_service.py",
        "backend/app/application/login_tracking.py",
        "backend/app/application/plan_management.py",
    }

    assert "**" in dockerignore.splitlines()
    assert excluded.issubset(set(dockerignore.splitlines()))
    assert {"**/__pycache__/", "**/*.pyc", "**/*.pyo"}.issubset(set(dockerignore.splitlines()))


def test_worker_requirements_do_not_include_web_or_development_packages() -> None:
    requirements = WORKER_REQUIREMENTS_PATH.read_text(encoding="utf-8")
    normalized = {
        line.split("==", 1)[0].strip().lower()
        for line in requirements.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert normalized.isdisjoint(
        {
            "fastapi",
            "uvicorn",
            "python-multipart",
            "pytest",
            "ruff",
            "httpx",
            "alembic",
            "sqlalchemy",
        }
    )


def test_worker_conversions_and_both_handlers_work_with_sqlite_imports_blocked() -> None:
    script = r'''
import importlib.abc
import json
import os
import sys

class NoSQLite(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "_sqlite3" or fullname == "sqlite3" or fullname.startswith("sqlite3."):
            raise AssertionError("Worker must not require SQLite: " + fullname)

assert "sqlite3" not in sys.modules and "_sqlite3" not in sys.modules
sys.meta_path.insert(0, NoSQLite())
os.environ.update(TEXTRACT_ENABLED="false", PDF_OCR_ENABLED="false", AWS_EC2_METADATA_DISABLED="true")
import app.workers.conversion_lambda
import app.workers.ai_recovery_lambda
from app.workers.worker_image_canary import run_checks
print(json.dumps(run_checks()))
'''
    result = subprocess.run([sys.executable, "-c", script], cwd=BACKEND_ROOT, check=True,
                            capture_output=True, text=True, timeout=45)
    payload = json.loads(result.stdout)
    assert payload["status"] == "passed"
    assert payload["checks"] == ["csv", "xlsx", "ofx", "pdf", "pdfium", "psycopg"]


@pytest.mark.parametrize("canonical_enabled", ["false", "true"])
def test_worker_bootstrap_without_excluded_modules(canonical_enabled) -> None:
    script = r'''
import importlib.abc
import os
import sys

from app.workers.worker_image_contract import FORBIDDEN_MODULE_PREFIXES

class WorkerBoundary(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        excluded = (*FORBIDDEN_MODULE_PREFIXES, "sqlite3", "_sqlite3", "fastapi", "uvicorn")
        if any(fullname == prefix or fullname.startswith(prefix + ".") for prefix in excluded):
            raise ModuleNotFoundError("Excluded from worker image: " + fullname, name=fullname)

sys.meta_path.insert(0, WorkerBoundary())
os.environ.update(
    DATABASE_URL="postgresql://synthetic:synthetic@127.0.0.1/worker_test",
    DATABASE_SCHEMA="public", CONVERSION_S3_BUCKET="synthetic-worker-test",
    CANONICAL_PRO_COMPLETION_ENABLED=sys.argv[1], PRO_ORGANIZATION_ENABLED="true",
    TEXTRACT_ENABLED="false", PDF_OCR_ENABLED="false", AWS_EC2_METADATA_DISABLED="true",
)
from app.workers import conversion_lambda as worker
from app.workers.worker_image_contract import verify_worker_imports
verify_worker_imports()

# Only external resources are replaced; the production bootstrap and its
# deferred imports run with the same forbidden modules as the packaged image.
class ExternalResource:
    def __init__(self, **kwargs):
        pass

worker.PostgresConversionAccessService = ExternalResource
worker.S3AnalysisStorage = ExternalResource
worker.S3ConversionDocumentStore = ExternalResource
worker.build_s3_canonical_layout_capture_service = lambda **kwargs: None
worker.build_ai_recovery_shadow_dispatcher = lambda: None
processor = worker.build_lambda_processor()
assert (processor.repository.canonical_completion is not None) == (sys.argv[1] == "true")
assert callable(processor.completion_resumer)
assert callable(processor.receipt_recorder)
print("Worker bootstrap passed")
'''
    result = subprocess.run([sys.executable, "-c", script, canonical_enabled], cwd=BACKEND_ROOT,
                            capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Worker bootstrap passed" in result.stdout


@pytest.mark.parametrize("filename", ["Dockerfile.lambda", "Dockerfile.lambda.debian"])
def test_worker_images_omit_unused_sqlite_library_and_python_modules(filename) -> None:
    dockerfile = (ROOT / filename).read_text(encoding="utf-8")
    native_packages = next(line for line in dockerfile.splitlines() if "for package in" in line)
    assert "libsqlite3-0" not in native_packages
    assert "/usr/local/lib/python3.12/sqlite3" in dockerfile
    assert "/usr/local/lib/python3.12/lib-dynload/_sqlite3*.so" in dockerfile
    assert "COPY --from=build /runtime-status/ /var/lib/dpkg/status.d/" in dockerfile
