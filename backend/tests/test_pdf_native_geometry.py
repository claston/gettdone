from __future__ import annotations

from io import BytesIO

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.application import pdf_parser
from app.application.default_conversion_pipeline import _build_pdf_processing_metrics
from app.application.document_extraction_models import ExtractedWord
from app.application.models import NormalizedTransaction
from app.application.normalization.pdf_native_geometry import (
    extract_native_pdf_geometry,
    reconstruct_geometry_lines,
)
from app.application.normalization.pdf_native_geometry_shadow import (
    NativeGeometryShadowObservation,
    compare_native_geometry_results,
    run_native_geometry_shadow,
    should_run_native_geometry_shadow,
)
from app.application.parsers.pdf.models import PdfParseResult
from app.application.pdf_layout_inference import PdfLayoutInference


def _positioned_pdf(commands: list[str]) -> bytes:
    writer = PdfWriter()
    font = writer._add_object(  # noqa: SLF001 - low-level privacy-safe PDF fixture
        DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
                NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
            }
        )
    )
    page = writer.add_blank_page(width=595, height=842)
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
    )
    stream = DecodedStreamObject()
    stream.set_data("\n".join(commands).encode("cp1252"))
    page[NameObject("/Contents")] = writer._add_object(stream)  # noqa: SLF001
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def _word(index: int, text: str, left: float, width: float, *, top: float = 0.1) -> ExtractedWord:
    return ExtractedWord(
        id=f"p1-w{index}",
        page_number=1,
        word_index=index,
        text=text,
        normalized_text=text,
        bbox={"left": left, "top": top, "width": width, "height": 0.02},
        font_size=10.0,
        rotation=0,
    )


def _parse_result(*transactions: NormalizedTransaction, balance_failed: int = 0) -> PdfParseResult:
    return PdfParseResult(
        transactions=list(transactions),
        canonical_transactions=None,
        layout=PdfLayoutInference(
            layout_name="synthetic_statement_v1",
            confidence=0.9,
            used_fallback=False,
        ),
        extracted_text="synthetic",
        parse_metrics={
            "selected_parser": "tabular",
            "balance_consistency_failed": balance_failed,
        },
    )


def test_reconstruct_geometry_lines_joins_tiny_fragments_and_preserves_amount() -> None:
    words = (
        _word(1, "P", 0.10, 0.010),
        _word(2, "IX", 0.1105, 0.018),
        _word(3, "RECEBIDO", 0.145, 0.085),
        _word(4, "-1.234,56", 0.72, 0.10),
    )

    lines, metrics = reconstruct_geometry_lines(words)

    assert len(lines) == 1
    assert lines[0].text == "PIX RECEBIDO                         -1.234,56"
    assert [word.text for word in lines[0].words] == ["P", "IX", "RECEBIDO", "-1.234,56"]
    assert metrics.fragment_merges == 1
    assert "-1.234,56" in lines[0].text


def test_extract_native_pdf_geometry_returns_normalized_bounding_boxes_and_deduplicates() -> None:
    raw_pdf = _positioned_pdf(
        [
            "BT /F1 10 Tf 1 0 0 1 48 790 Tm (DATA DESCRICAO VALOR) Tj ET",
            "BT /F1 10 Tf 1 0 0 1 48 770 Tm (08/10/2026 PIX RECEBIDO) Tj ET",
            "BT /F1 10 Tf 1 0 0 1 48 770 Tm (08/10/2026 PIX RECEBIDO) Tj ET",
            "BT /F1 10 Tf 1 0 0 1 450 770 Tm (-1.234,56) Tj ET",
        ]
    )

    extraction = extract_native_pdf_geometry(raw_pdf)

    assert extraction.page_texts
    assert "08/10/2026" in extraction.page_texts[0]
    assert "-1.234,56" in extraction.page_texts[0]
    assert extraction.metrics.duplicate_characters_removed > 0
    assert extraction.metrics.word_count >= 4
    assert all(
        0.0 <= value <= 1.0
        for page in extraction.document.pages
        for word in page.words
        for value in word.bbox.values()
    )


def test_compare_native_geometry_results_marks_equivalent_and_potential_gain() -> None:
    baseline_transaction = NormalizedTransaction(
        date="2026-10-08",
        description="PIX recebido cliente",
        amount=100.0,
        type="inflow",
    )
    additional_transaction = NormalizedTransaction(
        date="2026-10-09",
        description="Pagamento fornecedor",
        amount=-25.5,
        type="outflow",
    )

    equivalent = compare_native_geometry_results(
        baseline_result=_parse_result(baseline_transaction),
        baseline_error=None,
        geometry_result=_parse_result(baseline_transaction),
        geometry_error=None,
        duration_ms=12,
    )
    gain = compare_native_geometry_results(
        baseline_result=_parse_result(baseline_transaction),
        baseline_error=None,
        geometry_result=_parse_result(baseline_transaction, additional_transaction),
        geometry_error=None,
        duration_ms=18,
    )

    assert equivalent.classification == "equivalent"
    assert equivalent.matched_transactions == 1
    assert gain.classification == "potential_gain"
    assert gain.geometry_transactions == 2
    assert gain.matched_transactions == 1


def test_compare_native_geometry_results_detects_sign_conflict_and_rescue() -> None:
    baseline_transaction = NormalizedTransaction(
        date="2026-10-08",
        description="PIX cliente",
        amount=100.0,
        type="inflow",
    )
    conflicting_transaction = NormalizedTransaction(
        date="2026-10-08",
        description="PIX cliente",
        amount=-100.0,
        type="outflow",
    )

    conflict = compare_native_geometry_results(
        baseline_result=_parse_result(baseline_transaction),
        baseline_error=None,
        geometry_result=_parse_result(conflicting_transaction),
        geometry_error=None,
        duration_ms=8,
    )
    rescue = compare_native_geometry_results(
        baseline_result=None,
        baseline_error=ValueError("baseline failed"),
        geometry_result=_parse_result(baseline_transaction),
        geometry_error=None,
        duration_ms=8,
    )

    assert conflict.classification == "conflict"
    assert conflict.sign_conflicts == 1
    assert conflict.amount_conflicts == 1
    assert rescue.classification == "potential_rescue"
    assert rescue.baseline_status == "error"


def test_shadow_sampling_is_disabled_by_default_and_deterministic() -> None:
    assert not should_run_native_geometry_shadow(b"pdf", environment={})
    environment = {
        "PDF_NATIVE_GEOMETRY_SHADOW_ENABLED": "true",
        "PDF_NATIVE_GEOMETRY_SHADOW_PERCENT": "37",
    }

    first = should_run_native_geometry_shadow(b"same-pdf", environment=environment)
    second = should_run_native_geometry_shadow(b"same-pdf", environment=environment)

    assert first is second
    assert should_run_native_geometry_shadow(
        b"pdf",
        environment={
            "PDF_NATIVE_GEOMETRY_SHADOW_ENABLED": "true",
            "PDF_NATIVE_GEOMETRY_SHADOW_PERCENT": "100",
        },
    )


def test_shadow_skips_pdf_explicitly_without_native_text(monkeypatch) -> None:
    baseline_error = ValueError("scanned PDF")
    baseline_error._parse_observability = {"native_text_detected": 0}

    def unexpected_extraction(_raw_bytes):
        raise AssertionError("geometry extraction must not run")

    monkeypatch.setattr(
        "app.application.normalization.pdf_native_geometry_shadow.extract_native_pdf_geometry",
        unexpected_extraction,
    )

    observation = run_native_geometry_shadow(
        b"%PDF scanned",
        baseline_result=None,
        baseline_error=baseline_error,
        parse_page_texts=lambda *_args, **_kwargs: None,
    )

    assert observation.classification == "not_applicable"
    assert observation.geometry_status == "skipped_no_native_text"
    assert observation.duration_ms == 0


def test_pdf_parser_shadow_keeps_baseline_result_and_attaches_observation(
    monkeypatch,
) -> None:
    transaction = NormalizedTransaction(
        date="2026-10-08",
        description="PIX recebido",
        amount=100.0,
        type="inflow",
    )
    baseline = _parse_result(transaction)
    observation = NativeGeometryShadowObservation(
        classification="equivalent",
        baseline_status="ok",
        baseline_layout="synthetic_statement_v1",
        baseline_parser="tabular",
        baseline_transactions=1,
        baseline_balance_failed=0,
        geometry_status="ok",
        geometry_layout="synthetic_statement_v1",
        geometry_parser="tabular",
        geometry_transactions=1,
        geometry_balance_failed=0,
        duration_ms=11,
        matched_transactions=1,
        date_conflicts=0,
        amount_conflicts=0,
        sign_conflicts=0,
        geometry_error_type="",
    )
    monkeypatch.setattr(pdf_parser, "should_run_native_geometry_shadow", lambda _raw: True)
    monkeypatch.setattr(pdf_parser, "_parse_pdf_transactions_baseline", lambda *_args, **_kwargs: baseline)
    monkeypatch.setattr(pdf_parser, "run_native_geometry_shadow", lambda *_args, **_kwargs: observation)

    result = pdf_parser.parse_pdf_transactions(b"%PDF synthetic")

    assert result.transactions == baseline.transactions
    assert result.parse_metrics["native_geometry_shadow_classification"] == "equivalent"
    assert result.parse_metrics["native_geometry_shadow_duration_ms"] == 11


def test_pdf_parser_shadow_preserves_baseline_failure_and_attaches_observation(
    monkeypatch,
) -> None:
    baseline_error = ValueError("official parser failed")
    observation = NativeGeometryShadowObservation(
        classification="potential_rescue",
        baseline_status="error",
        baseline_layout="",
        baseline_parser="",
        baseline_transactions=0,
        baseline_balance_failed=0,
        geometry_status="ok",
        geometry_layout="synthetic_statement_v1",
        geometry_parser="tabular",
        geometry_transactions=1,
        geometry_balance_failed=0,
        duration_ms=13,
        matched_transactions=0,
        date_conflicts=0,
        amount_conflicts=0,
        sign_conflicts=0,
        geometry_error_type="",
    )

    def fail_baseline(*_args, **_kwargs):
        raise baseline_error

    monkeypatch.setattr(pdf_parser, "should_run_native_geometry_shadow", lambda _raw: True)
    monkeypatch.setattr(pdf_parser, "_parse_pdf_transactions_baseline", fail_baseline)
    monkeypatch.setattr(pdf_parser, "run_native_geometry_shadow", lambda *_args, **_kwargs: observation)

    with pytest.raises(ValueError, match="official parser failed") as raised:
        pdf_parser.parse_pdf_transactions(b"%PDF synthetic")

    assert raised.value is baseline_error
    assert raised.value._parse_observability["native_geometry_shadow_classification"] == "potential_rescue"


def test_pdf_processing_metrics_preserve_native_geometry_shadow_values() -> None:
    metrics = _build_pdf_processing_metrics(
        extension="pdf",
        parse_metrics={
            "page_count": 1,
            "selected_parser": "inline",
            "native_geometry_shadow_attempted": 1,
            "native_geometry_shadow_classification": "potential_gain",
            "native_geometry_shadow_duration_ms": 27,
        },
        parse_ms=1.0,
        classify_ms=2.0,
        normalize_ms=3.0,
        reconcile_ms=4.0,
        total_ms=10.0,
    )

    assert metrics is not None
    assert metrics["native_geometry_shadow_attempted"] == 1
    assert metrics["native_geometry_shadow_classification"] == "potential_gain"
    assert metrics["native_geometry_shadow_duration_ms"] == 27
