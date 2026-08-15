from pathlib import Path
from typing import BinaryIO

from pypdf import PasswordType, PdfReader

from app.application.errors import InvalidFileContentError

PASSWORD_PROTECTED_PDF_MESSAGE = "PDF is password protected. Remove the password and try again."


def open_pdf_reader(source: str | Path | BinaryIO) -> PdfReader:
    reader = PdfReader(source)
    if not reader.is_encrypted:
        return reader

    try:
        password_type = reader.decrypt("")
    except Exception as exc:
        raise _build_password_protected_pdf_error() from exc

    if password_type == PasswordType.NOT_DECRYPTED:
        raise _build_password_protected_pdf_error()
    return reader


def _build_password_protected_pdf_error() -> InvalidFileContentError:
    error = InvalidFileContentError(PASSWORD_PROTECTED_PDF_MESSAGE)
    setattr(error, "_pdf_structure_read_ok", True)
    setattr(error, "_native_text_extraction_ok", False)
    setattr(error, "_native_text_error_type", "FileNotDecryptedError")
    return error
