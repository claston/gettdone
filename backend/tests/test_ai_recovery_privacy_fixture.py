from __future__ import annotations

from datetime import UTC, datetime, timedelta
from io import BytesIO
from types import SimpleNamespace

from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.application.ai_recovery.privacy_fixture import CanonicalV3PrivacyFixtureBuilder
from app.application.ai_recovery.request_publishing import build_ai_recovery_request_artifacts
from app.application.ai_recovery.schemas import AIRecoveryVersionSet, NovaDiagnosticV1


def _diagnostic() -> NovaDiagnosticV1:
    return NovaDiagnosticV1.model_validate(
        {
            "schema_version": "nova_diagnostic_v1",
            "statement": {
                "schema_version": "nova_statement_v2",
                "document": {
                    "pages_examined": 1,
                    "all_pages_examined": True,
                    "transcription_truncated": False,
                    "visible_period": {"start_text": "01/09/2026", "end_text": "30/09/2026"},
                },
                "opening_balance": None,
                "closing_balance": None,
                "transactions": [
                    {
                        "visual_order": 1,
                        "page": 1,
                        "visual_line_start": 4,
                        "visual_line_end": 4,
                        "date_text": "01/09/2026",
                        "description_lines": ["PIX RECEBIDO"],
                        "amount_text": "125,50",
                        "direction": "credit",
                        "running_balance_text": "1.125,50",
                        "ambiguities": [],
                    }
                ],
                "unresolved_rows": [],
                "pages": [
                    {
                        "page": 1,
                        "transactions_observed": 1,
                        "repeated_header_observed": False,
                        "unresolved_rows_observed": 0,
                    }
                ],
                "warnings": [],
            },
            "conclusion": "parser_defect",
            "findings": [],
        }
    )


def _manifest():
    now = datetime(2026, 9, 28, tzinfo=UTC)
    return build_ai_recovery_request_artifacts(
        bucket="private-ai-recovery",
        analysis_id="an_fixture123",
        pdf_bytes=b"%PDF placeholder",
        page_count=1,
        deterministic_artifact={"transactions": []},
        source_evidence={"pages": []},
        parser_release="release-123",
        layout_profile="nubank_statement_ptbr",
        layout_family="nubank_conta_digital",
        statement_type="conta_digital_extrato",
        layout_confidence=0.98,
        issue_codes=("balance_consistency_failed",),
        ai=AIRecoveryVersionSet(
            model_id="us.amazon.nova-2-lite-v1:0",
            prompt_version="nova_transaction_diagnosis_v1",
            output_schema_version="nova_diagnostic_v1",
            comparator_version="statement_comparator_v1",
        ),
        created_at=now,
        expires_at=now + timedelta(days=1),
    ).manifest


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
    manifest = _manifest()
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
        manifest=manifest,
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
