import sys
from collections.abc import Iterator
from pathlib import Path
from shutil import copyfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import dependencies as dependencies_module  # noqa: E402
from app.application.access_control import AccessControlService  # noqa: E402
from app.application.access_control import access_control_helpers as helpers_module  # noqa: E402
from app.application.conversion.conversion_document_store import (  # noqa: E402
    FilesystemConversionDocumentStore,
)
from app.application.conversion.conversion_job_repository import (  # noqa: E402
    FilesystemConversionJobRepository,
)
from app.application.report_service import ReportService  # noqa: E402
from app.application.storage_service import TempAnalysisStorage  # noqa: E402

TEST_PASSWORD_HASH_ITERATIONS = 1


@pytest.fixture(scope="session", autouse=True)
def _isolate_default_filesystem_runtime(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Give each pytest/xdist process its own default runtime storage."""
    runtime_root = tmp_path_factory.mktemp("default-runtime")
    analysis_storage = TempAnalysisStorage(
        root_dir=runtime_root / "analyses",
        ttl_seconds=86_400,
    )
    document_store = FilesystemConversionDocumentStore(
        root_dir=runtime_root / "conversion-jobs" / "documents",
    )
    job_repository = FilesystemConversionJobRepository(
        root_dir=runtime_root / "conversion-jobs" / "registry",
    )

    patcher = pytest.MonkeyPatch()
    patcher.setenv("ACCESS_CONTROL_STATE_DIR", str(runtime_root / "access-control"))
    patcher.setattr(dependencies_module, "_storage", analysis_storage)
    patcher.setattr(dependencies_module, "_report_service", ReportService(storage=analysis_storage))
    patcher.setattr(dependencies_module, "_conversion_document_store", document_store)
    patcher.setattr(dependencies_module, "_conversion_job_repository", job_repository)
    patcher.setattr(dependencies_module, "_conversion_job_cleanup_service", None)
    patcher.setattr(dependencies_module, "_access_control_service", None)
    try:
        yield
    finally:
        dependencies_module.close_access_control_service()
        patcher.undo()


@pytest.fixture(scope="session", autouse=True)
def _optimize_access_control_test_runtime(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Avoid repeating production-cost hashing and SQLite bootstrap in unrelated tests."""
    template_dir = tmp_path_factory.mktemp("access-control-template")
    template_service = AccessControlService(
        state_file=template_dir / "template-state.json",
        token_secret="test-template-secret",
    )
    template_service.close()
    template_db = template_service.db_file
    original_init_db = AccessControlService._init_db

    def _init_from_template(service: AccessControlService) -> None:
        if service._use_postgres or hasattr(service, "_test_conn") or service.db_file.exists():
            original_init_db(service)
            return
        copyfile(template_db, service.db_file)

    patcher = pytest.MonkeyPatch()
    patcher.setattr(AccessControlService, "_init_db", _init_from_template)
    patcher.setattr(
        helpers_module,
        "PASSWORD_HASH_ITERATIONS",
        TEST_PASSWORD_HASH_ITERATIONS,
    )
    try:
        yield
    finally:
        patcher.undo()


@pytest.fixture(autouse=True)
def _restore_production_password_hash_for_marked_tests(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if request.node.get_closest_marker("production_password_hash") is not None:
        monkeypatch.setattr(
            helpers_module,
            "PASSWORD_HASH_ITERATIONS",
            helpers_module.PRODUCTION_PASSWORD_HASH_ITERATIONS,
        )
