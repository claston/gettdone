from app.api.conversion.conversion_observability import (
    _build_failure_diagnostics,
    _resolve_error_observability,
    _resolve_failed_conversion_code,
)
from app.application import InvalidFileContentError


def test_failure_diagnostics_marks_pdf_read_failure() -> None:
    diagnostics = _build_failure_diagnostics(InvalidFileContentError("Unable to read PDF bytes."))
    assert diagnostics["pdf_read_ok"] is False
    assert diagnostics["text_extracted_likely"] is False


def test_failure_diagnostics_distinguishes_pdf_structure_from_native_text_extraction() -> None:
    error = InvalidFileContentError("Unable to extract native PDF text.")
    setattr(error, "_pdf_structure_read_ok", True)
    setattr(error, "_native_text_extraction_ok", False)
    setattr(error, "_native_text_error_type", "TypeError")

    diagnostics = _build_failure_diagnostics(error)

    assert diagnostics["pdf_read_ok"] is False
    assert diagnostics["pdf_structure_read_ok"] is True
    assert diagnostics["native_text_extraction_ok"] is False
    assert diagnostics["native_text_error_type"] == "TypeError"
    assert "native_text_extraction" in diagnostics["missing_signals"]


def test_native_text_extraction_failure_is_classified_as_invalid_pdf_content() -> None:
    error = InvalidFileContentError("Unable to extract native PDF text.")

    assert _resolve_failed_conversion_code(error) == "invalid_pdf_content"
    assert _resolve_error_observability(error) == (
        "native_pdf_read",
        "corrupted_pdf",
        "InvalidFileContentError",
    )


def test_failure_diagnostics_marks_missing_transaction_pattern() -> None:
    diagnostics = _build_failure_diagnostics(
        InvalidFileContentError("PDF text was extracted, but no recognizable transaction row pattern was found.")
    )
    assert diagnostics["pdf_read_ok"] is True
    assert diagnostics["text_extracted_likely"] is True
    assert "transaction_row_pattern" in diagnostics["missing_signals"]


def test_error_observability_maps_specific_pdf_parse_subcodes() -> None:
    stage, subcode, exc_class = _resolve_error_observability(
        InvalidFileContentError("PDF text was extracted, but transactions are in an unsupported table layout.")
    )
    assert stage == "parse"
    assert subcode == "unsupported_table_layout"
    assert exc_class == "InvalidFileContentError"


def test_failure_diagnostics_extracts_parser_signal_details() -> None:
    diagnostics = _build_failure_diagnostics(
        InvalidFileContentError(
            "PDF text was extracted, but no recognizable transaction row pattern was found. "
            "diagnostics: has_date_like=1 has_amount_like=0 inline_candidates=0 tabular_candidates=0 "
            "columnar_candidates=0 missing_signals=amount_pattern,transaction_row_pattern"
        )
    )
    assert diagnostics["has_date_like"] is True
    assert diagnostics["has_amount_like"] is False
    assert diagnostics["inline_candidates"] == 0
    assert diagnostics["tabular_candidates"] == 0
    assert diagnostics["columnar_candidates"] == 0
    assert "amount_pattern" in diagnostics["missing_signals"]
    assert "transaction_row_pattern" in diagnostics["missing_signals"]


def test_failure_diagnostics_includes_attached_parse_observability() -> None:
    exc = InvalidFileContentError("PDF text was extracted, but no recognizable transaction row pattern was found.")
    setattr(
        exc,
        "_parse_observability",
        {
            "textract_attempted": 1,
            "textract_used": 0,
            "textract_error_type": "InvalidFileContentError",
            "native_text_detected": 0,
        },
    )

    diagnostics = _build_failure_diagnostics(exc)

    assert diagnostics["textract_attempted"] == 1
    assert diagnostics["textract_used"] == 0
    assert diagnostics["textract_error_type"] == "InvalidFileContentError"
    assert diagnostics["native_text_detected"] == 0
