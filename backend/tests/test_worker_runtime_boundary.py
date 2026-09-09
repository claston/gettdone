import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
DOCKERFILE_PATH = ROOT / "Dockerfile.lambda"
DOCKERIGNORE_PATH = ROOT / "Dockerfile.lambda.dockerignore"
WORKER_REQUIREMENTS_PATH = BACKEND_ROOT / "requirements-worker.txt"
WORKER_CONTRACT_PATH = BACKEND_ROOT / "app" / "workers" / "worker_image_contract.py"


def _imported_app_modules() -> set[str]:
    command = (
        "import json, sys; "
        "import app.workers.conversion_lambda; "
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
    assert "python -m app.workers.worker_image_contract" in dockerfile
    assert '"app/application/admin_dashboard_service.py"' in worker_contract
    assert '"app.application.admin_dashboard_service"' in worker_contract


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
