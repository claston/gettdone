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
        if analysis_id != "an_test123":
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


def build_client() -> TestClient:
    report_service = FakeReportService()
    access_control_service = FakeAccessControlService()
    app.dependency_overrides[get_report_service] = lambda: report_service
    app.dependency_overrides[get_access_control_service] = lambda: access_control_service
    return TestClient(app)


def test_analyze_route_is_disabled() -> None:
    client = build_client()
    response = client.post(
        "/analyze",
        files={"file": ("sample.csv", b"date,description,amount\n2026-04-01,TEST,-20.0", "text/csv")},
    )

    assert response.status_code == 405
    app.dependency_overrides.clear()


def test_report_happy_path_and_not_found() -> None:
    client = build_client()

    ok = client.get("/report/an_test123", params={"anonymous_fingerprint": "fp-report-test"})
    assert ok.status_code == 200

    missing = client.get("/report/an_unknown", params={"anonymous_fingerprint": "fp-report-test"})
    assert missing.status_code == 404
    assert missing.json()["detail"] == "Analysis not found"
    app.dependency_overrides.clear()


def test_report_requires_identity_and_enforces_owner() -> None:
    client = build_client()

    no_identity = client.get("/report/an_test123")
    assert no_identity.status_code == 400
    report_service = app.dependency_overrides[get_report_service]()
    report_service.set_report_owner("an_test123", "anonymous", "anon_fp-owner")

    owner = client.get("/report/an_test123", params={"anonymous_fingerprint": "fp-owner"})
    assert owner.status_code == 200

    other = client.get("/report/an_test123", params={"anonymous_fingerprint": "fp-other"})
    assert other.status_code == 403
    assert other.json()["detail"] == "Access denied for this analysis."
    app.dependency_overrides.clear()
