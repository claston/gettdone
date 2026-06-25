from pathlib import Path
from tempfile import NamedTemporaryFile

from fastapi.testclient import TestClient

from app.dependencies import get_access_control_service, get_report_service
from app.main import app


class FakeReportService:
    def __init__(self) -> None:
        self._tmp = NamedTemporaryFile(mode="wb", suffix=".xlsx", delete=False)
        self._tmp.write(b"test-report")
        self._tmp.flush()
        self._path = Path(self._tmp.name)
        self._owners: dict[str, tuple[str, str]] = {}

    def get_report_path(self, analysis_id: str) -> Path:
        if analysis_id != "an_metrics123":
            from app.application import AnalysisNotFoundError

            raise AnalysisNotFoundError
        return self._path

    def set_report_owner(self, analysis_id: str, identity_type: str, identity_id: str) -> None:
        owner = self._owners.get(analysis_id)
        if owner is not None and owner != (identity_type, identity_id):
            from app.application import AnalysisAccessDeniedError

            raise AnalysisAccessDeniedError
        self._owners[analysis_id] = (identity_type, identity_id)

    def assert_report_owner(
        self,
        analysis_id: str,
        identity_type: str,
        identity_id: str,
        *,
        allow_unowned: bool = False,
    ) -> None:
        owner = self._owners.get(analysis_id)
        if owner is None:
            if allow_unowned:
                return
            from app.application import AnalysisAccessDeniedError

            raise AnalysisAccessDeniedError
        if owner != (identity_type, identity_id):
            from app.application import AnalysisAccessDeniedError

            raise AnalysisAccessDeniedError


class FakeAccessControlService:
    def resolve_identity(self, anonymous_fingerprint: str | None, user_token: str | None):
        class Identity:
            def __init__(self, identity_type: str, identity_id: str) -> None:
                self.identity_type = identity_type
                self.identity_id = identity_id

        token = (user_token or "").strip()
        fingerprint = (anonymous_fingerprint or "").strip()
        if token:
            return Identity("user", "usr_fake")
        if fingerprint:
            return Identity("anonymous", f"anon_{fingerprint}")
        from app.application import InvalidUserTokenError

        raise InvalidUserTokenError


def _build_client() -> TestClient:
    app.dependency_overrides[get_report_service] = lambda: FakeReportService()
    app.dependency_overrides[get_access_control_service] = lambda: FakeAccessControlService()
    return TestClient(app)


def test_analyze_route_is_disabled() -> None:
    client = _build_client()

    response = client.post(
        "/analyze",
        data={"anonymous_fingerprint": "fp-metrics"},
        files={"file": ("sample.pdf", b"%PDF data", "application/pdf")},
    )

    assert response.status_code == 405
    app.dependency_overrides.clear()


def test_report_negative_path_returns_not_found_for_unknown_analysis_id() -> None:
    client = _build_client()

    response = client.get("/report/an_unknown", params={"anonymous_fingerprint": "fp-owner"})
    assert response.status_code == 404
    assert response.json()["detail"] == "Analysis not found"
    app.dependency_overrides.clear()
