from pathlib import Path
from types import SimpleNamespace

from app.application.access_control import IdentityContext
from app.application.conversion.conversion_document_store import FilesystemConversionDocumentStore
from app.application.conversion.conversion_job_factory import ConversionJobFactory
from app.application.conversion.uploaded_document import ingest_uploaded_document


class FakeAccessControlService:
    def __init__(self) -> None:
        self.identity = IdentityContext(
            identity_type="user",
            identity_id="usr_123",
            quota_limit=10,
        )
        self.resolved: list[tuple[str | None, str | None]] = []

    def get_user_by_session_access_token(self, access_token: str):
        assert access_token == "session-token"
        return SimpleNamespace(token="resolved-user-token")

    def resolve_identity(self, *, anonymous_fingerprint: str | None, user_token: str | None):
        self.resolved.append((anonymous_fingerprint, user_token))
        return self.identity


def test_conversion_job_factory_resolves_identity_and_stores_document_reference(tmp_path: Path) -> None:
    access_control = FakeAccessControlService()
    document_store = FilesystemConversionDocumentStore(root_dir=tmp_path / "jobs")
    factory = ConversionJobFactory(
        access_control_service=access_control,
        document_store=document_store,
    )

    job = factory.create(
        document=ingest_uploaded_document("statement.csv", b"date,description,amount\n"),
        anonymous_fingerprint=None,
        user_token=None,
        authorization=None,
        access_cookie_token="session-token",
        scanned_likely=False,
        estimated_pages_count=None,
    )

    assert job.job_id.startswith("job_")
    assert job.identity == access_control.identity
    assert job.document.filename == "statement.csv"
    assert job.document.storage_key.startswith("doc_")
    assert access_control.resolved == [(None, "resolved-user-token")]
    assert not hasattr(job, "authorization")
    assert not hasattr(job, "access_cookie_token")
    assert not hasattr(job, "user_token")
