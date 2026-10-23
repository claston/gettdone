"""Application policy: readable movements remain exportable with review warnings."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace

from app.application.errors import PdfFinancialValidationError
from app.application.normalization.canonical_metrics import build_canonical_quality_metrics
from app.application.parsers.pdf.statement_completeness import validate_statement_completeness

_CONTROL_WARNINGS = ContextVar("statement_control_warnings", default=None)


@contextmanager
def statement_review_scope():
    warnings = []
    token = _CONTROL_WARNINGS.set(warnings)
    try:
        yield warnings
    finally:
        _CONTROL_WARNINGS.reset(token)


@contextmanager
def statement_review_attempt():
    """Retries keep their own warnings; standalone strict readers stay strict."""
    if _CONTROL_WARNINGS.get() is None:
        yield
    else:
        with statement_review_scope():
            yield


def financial_mismatch(message=None):
    """Only arithmetic checks may be softened; parsing/geometry guards still fail."""
    warnings = _CONTROL_WARNINGS.get()
    if warnings is None:
        raise PdfFinancialValidationError(message)
    warnings.append("financial_validation_failed")


def apply_statement_review(result, *, validation_failed=False):
    validation_failed = (validation_failed or bool(result.parse_metrics.get("statement_validation_failed"))
                         or bool(_CONTROL_WARNINGS.get()))
    pages = result.source_page_texts or ()
    partial = bool(result.parse_metrics.get("partial_statement")) or validate_statement_completeness(pages)
    if not partial and not validation_failed:
        return result
    warnings = ["manual_review_recommended"]
    if partial:
        warnings.append("partial_statement")
    if validation_failed:
        warnings.append("financial_validation_failed")
    canonical = list(result.canonical_transactions or [])
    if canonical:
        canonical[0] = replace(canonical[0], warnings=list(dict.fromkeys([*canonical[0].warnings, *warnings])))
    metrics = {**result.parse_metrics, **build_canonical_quality_metrics(canonical),
               "statement_validation_failed": int(validation_failed),
               "partial_statement": int(partial), "financial_controls_validated": 0,
               "confidence_band": "low", "balance_validation_status": "review_required",
               "export_recommendation": "review_recommended",
               "export_recommendation_reason": "partial_statement" if partial else "financial_validation_failed"}
    return replace(result, canonical_transactions=canonical, parse_metrics=metrics)
