from io import BytesIO

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.application.conversion import canonical_layout_capture
from app.application.conversion.canonical_layout_capture import (
    CanonicalLayoutCaptureService,
    CanonicalLayoutGenerator,
    CanonicalLayoutPrivacyError,
)
from app.application.conversion.uploaded_document import ingest_uploaded_document
from app.application.errors import InvalidFileContentError


class _RecordingStore:
    def __init__(self, *, fail: bool = False, failure: Exception | None = None) -> None:
        self.fail = fail
        self.failure = failure
        self.artifacts = []

    def store(self, artifact) -> None:
        if self.failure is not None:
            raise self.failure
        if self.fail:
            raise RuntimeError("simulated S3 failure")
        self.artifacts.append(artifact)


def _text_pdf(*lines: str) -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=595, height=842)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Courier"),
        }
    )
    font_ref = writer._add_object(font)
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})}
    )
    commands = ["BT", "/F1 10 Tf", "40 800 Td"]
    for index, line in enumerate(lines):
        if index:
            commands.append("0 -16 Td")
        escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        commands.append(f"({escaped}) Tj")
    commands.append("ET")
    stream = DecodedStreamObject()
    stream.set_data("\n".join(commands).encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def _capture_kwargs(**overrides) -> dict[str, object]:
    values: dict[str, object] = {
        "status": "Sucesso",
        "conversion_type": "pdf-ofx",
        "transactions_count": 1,
        "layout_name": "generic_statement_ptbr",
        "layout_confidence": 0.72,
        "selected_parser": "tabular",
        "warning_count": 0,
        "balance_failed": 0,
        "bank_name": None,
        "bank_code": None,
    }
    values.update(overrides)
    return values


def test_generator_builds_new_pdf_without_source_identity_or_numbers() -> None:
    source = _text_pdf(
        "BANCO TESTE",
        "CLIENTE MARIA SILVA CPF 123.456.789-09",
        "AGENCIA 4321 CONTA 98765-4",
        "DATA HISTORICO VALOR SALDO",
        "10/09/2026 PIX PARA JOAO -1.234,56 8.765,44",
    )
    document = ingest_uploaded_document("extrato-maria-98765.pdf", source)
    generator = CanonicalLayoutGenerator(capture_id_provider=lambda: "cap_0123456789abcdef01234567")

    artifact = generator.generate(document=document, **_capture_kwargs())

    assert artifact.privacy_validated is True
    assert artifact.pdf_bytes.startswith(b"%PDF")
    extracted = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(artifact.pdf_bytes)).pages)
    serialized = f"{extracted}\n{artifact.manifest}".upper()
    for forbidden in ("MARIA", "SILVA", "JOAO", "98765", "4321", "123.456.789-09", "10/09/2026"):
        assert forbidden not in serialized
    assert "DATA" in extracted
    assert "HISTORICO" in extracted
    assert "VALOR" in extracted
    assert "SALDO" in extracted
    assert "DADO" in extracted
    assert artifact.manifest["schema_version"] == "1"
    assert artifact.manifest["text_source"] == "native"
    assert artifact.manifest["quality"]["reason_codes"] == [
        "generic_layout",
        "layout_confidence_below_95",
    ]
    assert "extrato-maria" not in str(artifact.manifest).lower()


def test_generator_preserves_catalog_bank_as_explicit_manifest_identity() -> None:
    source = _text_pdf(
        "ITAU",
        "CLIENTE MARIA SILVA",
        "DATA HISTORICO VALOR",
        "10/09/2026 PIX 10,00",
    )
    document = ingest_uploaded_document("statement.pdf", source)
    generator = CanonicalLayoutGenerator(capture_id_provider=lambda: "cap_0123456789abcdef01234567")

    artifact = generator.generate(
        document=document,
        **_capture_kwargs(bank_name="Itau", bank_code="341"),
    )

    assert artifact.manifest["bank"] == {
        "code": "341",
        "name": "Itaú",
        "catalog_match": True,
        "detection_source": "conversion",
    }


def test_generator_preserves_unlisted_cooperative_identity_but_not_holder() -> None:
    source = _text_pdf(
        "COOPERATIVA DE CREDITO VALE VERDE",
        "CLIENTE MARIA SILVA CPF 123.456.789-09",
        "AGENCIA 4321 CONTA 98765-4",
        "DATA HISTORICO VALOR",
        "10/09/2026 PIX PARA JOAO 10,00",
    )
    document = ingest_uploaded_document("statement.pdf", source)
    generator = CanonicalLayoutGenerator(capture_id_provider=lambda: "cap_0123456789abcdef01234567")

    artifact = generator.generate(document=document, **_capture_kwargs())

    assert artifact.manifest["bank"] == {
        "code": None,
        "name": "COOPERATIVA DE CREDITO VALE VERDE",
        "catalog_match": False,
        "detection_source": "header",
    }
    extracted = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(artifact.pdf_bytes)).pages)
    assert "COOPERATIVA" in extracted
    assert "VALE" in extracted
    assert "VERDE" in extracted
    assert "MARIA" not in extracted
    assert "JOAO" not in extracted
    assert "98765" not in extracted


def test_institution_tokens_are_preserved_only_on_the_bank_header_line() -> None:
    source = _text_pdf(
        "BANCO VALE SEGURO",
        "CLIENTE MARIA VALE",
        "DATA HISTORICO VALOR",
        "10/09/2026 PIX 10,00",
    )
    document = ingest_uploaded_document("statement.pdf", source)
    generator = CanonicalLayoutGenerator(capture_id_provider=lambda: "cap_0123456789abcdef01234567")

    artifact = generator.generate(document=document, **_capture_kwargs())

    extracted_lines = [
        line.strip()
        for page in PdfReader(BytesIO(artifact.pdf_bytes)).pages
        for line in (page.extract_text() or "").splitlines()
        if line.strip()
    ]
    assert "BANCO VALE SEGURO" in extracted_lines
    client_line = next(line for line in extracted_lines if line.startswith("CLIENTE"))
    assert client_line == "CLIENTE DADO DADO"


def test_privacy_validation_does_not_treat_manifest_keys_as_source_leaks() -> None:
    source = _text_pdf(
        "STATUS DATA VALOR",
        "10/09/2026 PIX 10,00",
    )
    document = ingest_uploaded_document("statement.pdf", source)
    generator = CanonicalLayoutGenerator(capture_id_provider=lambda: "cap_0123456789abcdef01234567")

    artifact = generator.generate(document=document, **_capture_kwargs())

    extracted = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(artifact.pdf_bytes)).pages)
    assert "STATUS" not in extracted
    assert "DADO" in extracted


def test_privacy_validation_still_blocks_actual_source_text_leak(monkeypatch) -> None:
    source = _text_pdf(
        "CLIENTE MARIA SILVA",
        "DATA VALOR",
        "10/09/2026 10,00",
    )
    document = ingest_uploaded_document("statement.pdf", source)
    generator = CanonicalLayoutGenerator(capture_id_provider=lambda: "cap_0123456789abcdef01234567")

    monkeypatch.setattr(
        canonical_layout_capture,
        "_sanitize_token",
        lambda raw_token, *, number_index, institution_tokens: (raw_token, "unsafe-test", number_index),
    )

    with pytest.raises(CanonicalLayoutPrivacyError, match="source_text_leak_detected"):
        generator.generate(document=document, **_capture_kwargs())


def test_generator_changes_even_single_digit_values_and_the_default_synthetic_date() -> None:
    source = _text_pdf(
        "DATA VALOR",
        "03/01/2001 7",
    )
    document = ingest_uploaded_document("statement.pdf", source)
    generator = CanonicalLayoutGenerator(capture_id_provider=lambda: "cap_0123456789abcdef01234567")

    artifact = generator.generate(document=document, **_capture_kwargs())

    extracted = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(artifact.pdf_bytes)).pages)
    assert "03/01/2001" not in extracted
    assert "\n7\n" not in f"\n{extracted}\n"


def test_capture_stores_review_but_never_clean_pdf() -> None:
    store = _RecordingStore()
    service = CanonicalLayoutCaptureService(
        enabled=True,
        generator=CanonicalLayoutGenerator(capture_id_provider=lambda: "cap_0123456789abcdef01234567"),
        store=store,
    )
    document = ingest_uploaded_document("statement.pdf", _text_pdf("DATA HISTORICO VALOR", "10/09/2026 PIX 10,00"))

    review = service.capture(document=document, **_capture_kwargs())
    clean = service.capture(
        document=document,
        **_capture_kwargs(
            layout_name="nubank_statement_ptbr",
            layout_confidence=0.98,
            selected_parser="inline",
        ),
    )

    assert review.status == "stored"
    assert clean.status == "not_eligible"
    assert clean.reason == "clean_conversion"
    assert len(store.artifacts) == 1


def test_capture_failure_is_best_effort_and_contains_no_error_detail() -> None:
    service = CanonicalLayoutCaptureService(
        enabled=True,
        generator=CanonicalLayoutGenerator(capture_id_provider=lambda: "cap_0123456789abcdef01234567"),
        store=_RecordingStore(fail=True),
    )
    document = ingest_uploaded_document("statement.pdf", _text_pdf("DATA VALOR", "10/09/2026 10,00"))

    result = service.capture(document=document, **_capture_kwargs())

    assert result.status == "upload_failed"
    assert result.reason == "RuntimeError"
    assert "simulated" not in str(result)


def test_capture_reports_safe_aws_error_code_without_error_message(caplog) -> None:
    class S3ClientError(Exception):
        response = {
            "Error": {
                "Code": "AccessDenied",
                "Message": "secret bucket and object details must not be logged",
            }
        }

    caplog.set_level("WARNING", logger="app.application.conversion.canonical_layout_capture")
    service = CanonicalLayoutCaptureService(
        enabled=True,
        generator=CanonicalLayoutGenerator(capture_id_provider=lambda: "cap_0123456789abcdef01234567"),
        store=_RecordingStore(failure=S3ClientError()),
    )
    document = ingest_uploaded_document("statement.pdf", _text_pdf("DATA VALOR", "10/09/2026 10,00"))

    result = service.capture(document=document, **_capture_kwargs())

    assert result.status == "upload_failed"
    assert result.reason == "AccessDenied"
    assert "secret" not in str(result)
    assert "canonical_layout_upload_failed error_type=S3ClientError reason=AccessDenied" in caplog.text
    assert "secret" not in caplog.text


def test_capture_skips_pdf_without_native_text() -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    output = BytesIO()
    writer.write(output)
    service = CanonicalLayoutCaptureService(
        enabled=True,
        generator=CanonicalLayoutGenerator(capture_id_provider=lambda: "cap_0123456789abcdef01234567"),
        store=_RecordingStore(),
    )

    result = service.capture(
        document=ingest_uploaded_document("scanned.pdf", output.getvalue()),
        **_capture_kwargs(status="Falha", transactions_count=0, selected_parser=None),
    )

    assert result.status == "skipped_unsupported"
    assert result.reason == "native_text_unavailable"


def test_capture_reuses_ocr_page_texts_when_pdf_has_no_native_text() -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    output = BytesIO()
    writer.write(output)
    store = _RecordingStore()
    generator = CanonicalLayoutGenerator(
        capture_id_provider=lambda: "cap_0123456789abcdef01234567",
        ocr_page_text_extractor=lambda _raw_bytes: pytest.fail("reused OCR text must avoid a second OCR pass"),
    )
    service = CanonicalLayoutCaptureService(enabled=True, generator=generator, store=store)

    result = service.capture(
        document=ingest_uploaded_document("scanned.pdf", output.getvalue()),
        page_texts=("ITAU\nCLIENTE MARIA SILVA\nDATA HISTORICO VALOR\n10/09/2026 PIX 10,00",),
        page_text_source="ocr",
        **_capture_kwargs(),
    )

    assert result.status == "stored"
    assert len(store.artifacts) == 1
    artifact = store.artifacts[0]
    assert artifact.manifest["text_source"] == "ocr"
    assert len(artifact.manifest["pages"]) == 1
    serialized = str(artifact.manifest).upper()
    assert "MARIA" not in serialized
    assert "SILVA" not in serialized
    assert "10/09/2026" not in serialized
    extracted = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(artifact.pdf_bytes)).pages)
    assert "MARIA" not in extracted
    assert "SILVA" not in extracted


def test_generator_runs_second_ocr_pass_when_reused_page_texts_are_unavailable() -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    output = BytesIO()
    writer.write(output)
    calls: list[bytes] = []

    def extract_with_ocr(raw_bytes: bytes) -> list[str]:
        calls.append(raw_bytes)
        return ["SANTANDER\nDATA HISTORICO VALOR\n10/09/2026 PIX 10,00"]

    generator = CanonicalLayoutGenerator(
        capture_id_provider=lambda: "cap_0123456789abcdef01234567",
        ocr_page_text_extractor=extract_with_ocr,
    )

    artifact = generator.generate(
        document=ingest_uploaded_document("scanned.pdf", output.getvalue()),
        **_capture_kwargs(bank_name="Santander", bank_code="033"),
    )

    assert calls == [output.getvalue()]
    assert artifact.manifest["text_source"] == "ocr"
    assert artifact.manifest["bank"]["code"] == "033"


def test_capture_keeps_native_text_unavailable_when_second_ocr_pass_cannot_run() -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    output = BytesIO()
    writer.write(output)

    def unavailable_ocr(_raw_bytes: bytes) -> list[str]:
        raise InvalidFileContentError("OCR disabled")

    service = CanonicalLayoutCaptureService(
        enabled=True,
        generator=CanonicalLayoutGenerator(ocr_page_text_extractor=unavailable_ocr),
        store=_RecordingStore(),
    )

    result = service.capture(
        document=ingest_uploaded_document("scanned.pdf", output.getvalue()),
        **_capture_kwargs(),
    )

    assert result.status == "skipped_unsupported"
    assert result.reason == "native_text_unavailable"


def test_disabled_capture_does_not_inspect_document() -> None:
    service = CanonicalLayoutCaptureService(enabled=False)

    result = service.capture(
        document=ingest_uploaded_document("invalid.pdf", b"not a real PDF"),
        **_capture_kwargs(),
    )

    assert result.status == "disabled"
    assert result.reason == "feature_disabled"
