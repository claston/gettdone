from io import BytesIO

from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.application.conversion.canonical_layout_capture import (
    CanonicalLayoutCaptureService,
    CanonicalLayoutGenerator,
)
from app.application.conversion.uploaded_document import ingest_uploaded_document


class _RecordingStore:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.artifacts = []

    def store(self, artifact) -> None:
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
    assert artifact.manifest["quality"]["reason_codes"] == [
        "generic_layout",
        "layout_confidence_below_95",
    ]
    assert "extrato-maria" not in str(artifact.manifest).lower()


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


def test_disabled_capture_does_not_inspect_document() -> None:
    service = CanonicalLayoutCaptureService(enabled=False)

    result = service.capture(
        document=ingest_uploaded_document("invalid.pdf", b"not a real PDF"),
        **_capture_kwargs(),
    )

    assert result.status == "disabled"
