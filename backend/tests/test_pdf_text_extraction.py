from io import BytesIO

import pytest
from fastapi import HTTPException
from pypdf import PdfWriter

from app.api.conversion.conversion_error_mapper import _raise_http_convert_error
from app.api.conversion.conversion_observability import (
    _build_failure_diagnostics,
    _resolve_error_observability,
    _resolve_failed_conversion_code,
)
from app.application.conversion.document_conversion_pipeline import (
    _build_failure_diagnostics as _build_pipeline_failure_diagnostics,
)
from app.application.errors import InvalidFileContentError
from app.application.parsers.pdf.text_extraction import read_pdf_creation_month_year
from app.application.pdf_parser import parse_pdf_transactions


def _build_encrypted_pdf(*, password: str) -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.add_metadata({"/CreationDate": "D:20250801000000"})
    writer.encrypt(user_password=password)
    payload = BytesIO()
    writer.write(payload)
    return payload.getvalue()


def test_metadata_reader_rejects_password_protected_pdf_with_domain_error() -> None:
    raw_bytes = _build_encrypted_pdf(password="secret")

    with pytest.raises(InvalidFileContentError, match="password"):
        read_pdf_creation_month_year(raw_bytes)


def test_parser_rejects_password_protected_pdf_before_ocr_fallback() -> None:
    raw_bytes = _build_encrypted_pdf(password="secret")

    with pytest.raises(InvalidFileContentError, match="password") as exc_info:
        parse_pdf_transactions(raw_bytes)

    diagnostics = _build_failure_diagnostics(exc_info.value)
    pipeline_diagnostics = _build_pipeline_failure_diagnostics(exc_info.value)
    assert _resolve_failed_conversion_code(exc_info.value) == "password_protected_pdf"
    assert _resolve_error_observability(exc_info.value) == (
        "native_pdf_read",
        "password_protected_pdf",
        "InvalidFileContentError",
    )
    assert diagnostics["pdf_read_ok"] is False
    assert diagnostics["pdf_structure_read_ok"] is True
    assert diagnostics["native_text_extraction_ok"] is False
    assert diagnostics["native_text_error_type"] == "FileNotDecryptedError"
    assert pipeline_diagnostics["pdf_read_ok"] is False
    assert pipeline_diagnostics["pdf_structure_read_ok"] is True
    assert pipeline_diagnostics["native_text_extraction_ok"] is False
    assert pipeline_diagnostics["native_text_error_type"] == "FileNotDecryptedError"


def test_metadata_reader_opens_pdf_encrypted_with_empty_password() -> None:
    raw_bytes = _build_encrypted_pdf(password="")

    assert read_pdf_creation_month_year(raw_bytes) == (8, 2025)


def test_json_error_mapper_returns_password_protected_pdf_detail() -> None:
    error = InvalidFileContentError("PDF is password protected. Remove the password and try again.")

    with pytest.raises(HTTPException) as exc_info:
        _raise_http_convert_error(error, identity=None, access_control_service=None)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == {
        "code": "password_protected_pdf",
        "message": "O arquivo parece estar protegido por senha.",
    }
