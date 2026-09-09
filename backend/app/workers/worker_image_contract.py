from __future__ import annotations

import sys
from importlib import metadata
from pathlib import Path

FORBIDDEN_MODULE_PREFIXES = (
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

FORBIDDEN_IMAGE_PATHS = (
    "app/api",
    "app/routers",
    "app/main.py",
    "app/dependencies.py",
    "app/security_baseline.py",
    "app/application/admin_dashboard_service.py",
    "app/application/access_control",
    "app/application/checkout_management.py",
    "app/application/contact_service.py",
    "app/application/google_oauth_service.py",
    "app/application/login_tracking.py",
    "app/application/plan_management.py",
)

FORBIDDEN_DISTRIBUTIONS = (
    "alembic",
    "fastapi",
    "httpx",
    "pytest",
    "python-multipart",
    "ruff",
    "sqlalchemy",
    "uvicorn",
)


def verify_worker_image(root: Path | None = None) -> None:
    image_root = (root or Path("/var/task")).resolve()
    present_paths = [relative for relative in FORBIDDEN_IMAGE_PATHS if (image_root / relative).exists()]
    if present_paths:
        raise RuntimeError(f"Worker image contains forbidden application paths: {present_paths}")

    from app.workers import conversion_lambda  # noqa: F401

    imported_forbidden = sorted(
        module
        for module in sys.modules
        if any(module == prefix or module.startswith(f"{prefix}.") for prefix in FORBIDDEN_MODULE_PREFIXES)
    )
    if imported_forbidden:
        raise RuntimeError(f"Worker import loaded forbidden modules: {imported_forbidden}")

    installed_forbidden: list[str] = []
    for distribution in FORBIDDEN_DISTRIBUTIONS:
        try:
            metadata.version(distribution)
        except metadata.PackageNotFoundError:
            continue
        installed_forbidden.append(distribution)
    if installed_forbidden:
        raise RuntimeError(f"Worker image contains forbidden distributions: {installed_forbidden}")

    from app.application.default_conversion_pipeline import build_default_conversion_pipeline

    smoke_result = build_default_conversion_pipeline().run(
        filename="worker-contract.csv",
        raw_bytes=b"date,description,amount\n2026-09-01,WORKER CONTRACT,10.00\n",
        analysis_id="an_workercontract",
    )
    analysis = smoke_result.analysis_data
    if analysis.transactions_total != 1 or analysis.preview_transactions[0].description != "WORKER CONTRACT":
        raise RuntimeError("Worker image failed the shared conversion core smoke test.")


if __name__ == "__main__":
    verify_worker_image()
    print("Worker image contract passed.")
