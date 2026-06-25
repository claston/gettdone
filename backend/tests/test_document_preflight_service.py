from io import BytesIO

from pypdf import PdfWriter

from app.application.access_control import AccessControlService
from app.application.conversion.document_preflight_service import (
    DocumentPreflightResult,
    DocumentPreflightService,
)
from app.application.errors import MaxPagesPerFileExceededError


def _build_pdf_with_pages(page_count: int) -> bytes:
    writer = PdfWriter()
    for _ in range(max(1, int(page_count))):
        writer.add_blank_page(width=612, height=792)
    payload = BytesIO()
    writer.write(payload)
    return payload.getvalue()


def test_inspect_raw_bytes_flags_blank_pdf_as_scanned_and_counts_pages() -> None:
    service = DocumentPreflightService()

    result = service.inspect_raw_bytes(filename="statement.pdf", raw_bytes=_build_pdf_with_pages(2))

    assert result == DocumentPreflightResult(scanned_likely=True, estimated_pages_count=2)


def test_build_policy_rejects_pdf_above_scanned_pages_limit(tmp_path) -> None:
    access_control = AccessControlService(
        state_file=tmp_path / "access-control-state.json",
        token_secret="test-secret",
    )
    identity = access_control.resolve_identity(anonymous_fingerprint="fp-preflight", user_token="")
    service = DocumentPreflightService()

    try:
        service.build_policy(
            identity=identity,
            filename="statement.pdf",
            staged_upload_size_bytes=1024,
            preflight_result=DocumentPreflightResult(scanned_likely=True, estimated_pages_count=11),
        )
    except MaxPagesPerFileExceededError as exc:
        assert exc.pages_count == 11
        assert exc.max_pages_per_file == 10
        assert exc.ocr_context == "scanned_pdf"
    else:
        raise AssertionError("Expected MaxPagesPerFileExceededError")
