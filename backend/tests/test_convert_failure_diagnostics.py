from app.application import InvalidFileContentError
from app.routers.upload import _build_failure_diagnostics, _resolve_error_observability


def test_failure_diagnostics_marks_pdf_read_failure() -> None:
    diagnostics = _build_failure_diagnostics(InvalidFileContentError("Unable to read PDF bytes."))
    assert diagnostics["pdf_read_ok"] is False
    assert diagnostics["text_extracted_likely"] is False


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
