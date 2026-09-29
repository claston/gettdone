from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace

from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.application.ai_recovery.privacy_fixture import CanonicalV3PrivacyFixtureBuilder
from backend.tests.test_ai_recovery_diagnostic_processing import _diagnostic, _request_objects


def _pdf(*lines: str) -> bytes:
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
        commands.append(f"({line}) Tj")
    commands.append("ET")
    stream = DecodedStreamObject()
    stream.set_data("\n".join(commands).encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def test_fixture_is_built_only_from_privacy_validated_canonical_output() -> None:
    artifacts, _ = _request_objects()
    original = _pdf(
        "NUBANK",
        "CLIENTE FAR ENGENHARIA LTDA",
        "DATA DESCRICAO VALOR SALDO",
        "01/09/2026 PIX RECEBIDO FAR ENGENHARIA LTDA 125,50 1.125,50",
    )
    source_text = (
        "NUBANK\nCLIENTE FAR ENGENHARIA LTDA\n"
        "DATA DESCRICAO VALOR SALDO\n"
        "01/09/2026 PIX RECEBIDO FAR ENGENHARIA LTDA 125,50 1.125,50"
    )
    diagnostic = _diagnostic().model_copy(
        update={
            "statement": _diagnostic().statement.model_copy(
                update={
                    "transactions": [
                        _diagnostic().statement.transactions[0].model_copy(
                            update={
                                "description_lines": [
                                    "PIX RECEBIDO FAR ENGENHARIA LTDA"
                                ]
                            }
                        )
                    ]
                }
            )
        }
    )
    fixture = CanonicalV3PrivacyFixtureBuilder().build(
        original_pdf=original,
        manifest=artifacts.manifest,
        deterministic_artifact={"selected_parser": "grouped", "transactions": [{}]},
        source_evidence={
            "schema_version": "source_evidence_v1",
            "classification": "restricted",
            "pages": [{"page": 1, "text": source_text, "lines": []}],
        },
        diagnostic=diagnostic,
        comparison=SimpleNamespace(diagnoses=()),
    )

    visible = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(fixture.pdf_bytes)).pages)
    serialized = str(fixture.expected) + str(fixture.manifest)
    assert fixture.manifest["privacy_validated"] is True
    assert "FAR ENGENHARIA" not in visible
    assert "FAR ENGENHARIA" not in serialized
    assert "PIX RECEBIDO" in visible
    assert "[PARTE]" in visible
