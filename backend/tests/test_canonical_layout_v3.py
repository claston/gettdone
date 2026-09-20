from io import BytesIO

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.application.conversion.canonical_layout_capture import CanonicalLayoutGenerator
from app.application.conversion.uploaded_document import ingest_uploaded_document
from app.application.conversion.v3.layout import build_semantic_layout_pages
from app.application.conversion.v3.privacy import validate_v3_output
from app.application.document_extraction_models import ExtractedLine
from app.application.pdf_parser import parse_pdf_transactions


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


def _source_lines() -> tuple[str, ...]:
    return (
        "GERENCIADOR CAIXA",
        "EMPRESA EXEMPLO LTDA",
        "CNPJ: 12.345.678/0001-90",
        "Agencia: 03726 Conta: 000574634316-8",
        "Endereco: Rua das Flores, 123 - Sao Paulo - SP CEP 01234-567",
        "Saldo anterior ao periodo solicitado R$ 44.826,29 C",
        "Extrato no periodo de 01/11/2025 a 30/11/2025",
        "Data Data Efetiva Documento Historico Valor Saldo",
        "03/11/2025",
        "01/11 22:19",
        "012219",
        "1234",
        "ABC12345",
        "CRED PIX QR",
        "AZCX MC CC",
        "APLIC AUTOMATICA FUNDO",
        "BLOQ PARC ORDEM JUDICIAL",
        "MARIA DA SILVA CPF 987.654.321-00",
        "PIX RECEBIDO JOAO DA SILVA CPF 111.222.333-44",
        "CAIXA SEGURADORA CNPJ 00.360.305/0001-04",
        "FUNDO MUNICIPAL DE SAUDE 11.323.994/0001-01",
        "CARLA CAROLINA JESUS DOS SANTOS ***.380.468-**",
        "PREF MUN TESTE 46.523.155/0001-03 R$ 212.636,59 R$ 552.636,59 C",
        "E00360305202511012219ABCDEF123456",
        "R$ 15,20 R$ 44.841,49 C",
        "03/11/2025 03/11 11:57 031157 DEB PIX CHAVE - R$ 140,00 R$ 44.701,49 C",
        "Pagina 1 de 4",
    )


def test_v3_preserves_transaction_semantics_and_masks_identifiable_data() -> None:
    pages, signals = build_semantic_layout_pages(
        [{"width": 595.0, "height": 842.0, "source_text": "\n".join(_source_lines())}]
    )

    visible = "\n".join(element["text"] for element in pages[0]["elements"])
    for expected in (
        "GERENCIADOR CAIXA",
        "SALDO ANTERIOR AO PERIODO SOLICITADO R$ 44.826,29 C",
        "EXTRATO NO PERIODO DE 01/11/2025 A 30/11/2025",
        "DATA DATA EFETIVA DOCUMENTO HISTORICO VALOR SALDO",
        "03/11/2025",
        "01/11 22:19",
        "CRED PIX QR",
        "AZCX MC CC",
        "APLIC AUTOMATICA FUNDO",
        "BLOQ PARC ORDEM JUDICIAL",
        "R$ 15,20 R$ 44.841,49 C",
        "DEB PIX CHAVE - R$ 140,00 R$ 44.701,49 C",
        "PAGINA 1 DE 4",
    ):
        assert expected in visible

    for forbidden in (
        "EMPRESA EXEMPLO LTDA",
        "12.345.678/0001-90",
        "03726",
        "000574634316-8",
        "RUA DAS FLORES",
        "01234-567",
        "MARIA DA SILVA",
        "987.654.321-00",
        "JOAO DA SILVA",
        "111.222.333-44",
        "CAIXA SEGURADORA",
        "00.360.305/0001-04",
        "FUNDO MUNICIPAL DE SAUDE",
        "11.323.994/0001-01",
        "CARLA CAROLINA JESUS DOS SANTOS",
        "380.468",
        "PREF MUN TESTE",
        "46.523.155/0001-03",
        "E00360305202511012219ABCDEF123456",
        "012219",
        "1234",
        "ABC12345",
        "031157",
    ):
        assert forbidden not in visible

    assert "[TITULAR]" in visible
    assert "[CNPJ]" in visible
    assert "AGENCIA [AGENCIA] CONTA [CONTA]" in visible
    assert "[ENDERECO]" in visible
    assert "[PARTE] CPF [CPF]" in visible
    assert "PIX RECEBIDO [PARTE] CPF [CPF]" in visible
    assert "[PARTE] [CNPJ]" in visible
    assert "[PARTE] [CPF]" in visible
    assert "[PARTE] [CNPJ] R$ 212.636,59 R$ 552.636,59 C" in visible
    assert "[IDENTIFICADOR]" in visible
    assert signals["privacy_mode"] == "semantic_transaction_v3"


def test_v3_preserves_textract_geometry() -> None:
    pages, signals = build_semantic_layout_pages(
        [{"width": 595.0, "height": 842.0, "source_text": "GERENCIADOR CAIXA"}],
        source_layout_lines=((ExtractedLine(
            id="header",
            page_number=1,
            line_index=1,
            text="GERENCIADOR CAIXA",
            bbox={"left": 0.12, "top": 0.08, "width": 0.4, "height": 0.03},
        ),),),
    )

    assert pages[0]["position_source"] == "textract"
    assert pages[0]["elements"][0]["x"] == 0.12
    assert pages[0]["elements"][0]["y"] == 0.08
    assert signals["geometry_source"] == "textract"


def test_v3_preserves_split_table_headers_and_following_history() -> None:
    pages, _ = build_semantic_layout_pages(
        [{
            "width": 595.0,
            "height": 842.0,
            "source_text": (
                "EXTRATO NO PERIODO DE 01/03/2026 A 31/03/2026\n"
                "Data\nData Efetiva D\n ocumento Historico Valor Saldo\n"
                "06/03/2026\n06/03 10:33\nCREDITO TRANSF INTERNET"
            ),
        }]
    )

    visible = "\n".join(element["text"] for element in pages[0]["elements"])
    assert "DATA\nDATA EFETIVA D\nOCUMENTO HISTORICO VALOR SALDO" in visible
    assert "CREDITO TRANSF INTERNET" in visible


@pytest.mark.parametrize("bank_name", ("NUBANK", "BANCO DO BRASIL", "SANTANDER", "ITAU"))
def test_v3_preserves_public_bank_names(bank_name: str) -> None:
    pages, _ = build_semantic_layout_pages(
        [{"width": 595.0, "height": 842.0, "source_text": f"{bank_name}\nCLIENTE MARIA DA SILVA"}]
    )

    visible = "\n".join(element["text"] for element in pages[0]["elements"])
    assert bank_name in visible
    assert "MARIA DA SILVA" not in visible


def test_v3_does_not_treat_an_unknown_company_with_bank_word_as_the_institution() -> None:
    pages, _ = build_semantic_layout_pages(
        [{"width": 595.0, "height": 842.0, "source_text": "BANCO DE ALIMENTOS SOLIDARIOS"}]
    )

    assert pages[0]["elements"][0]["text"] == "[TITULAR]"


def test_v3_generator_writes_semantic_pdf_and_manifest_without_pii() -> None:
    source = _text_pdf(*_source_lines())
    generator = CanonicalLayoutGenerator(
        schema_version="3",
        capture_id_provider=lambda: "cap_0123456789abcdef01234567",
    )

    artifact = generator.generate(
        document=ingest_uploaded_document("statement.pdf", source),
        status="Revisao",
        conversion_type="pdf-ofx",
        transactions_count=2,
        layout_name="caixa_gerenciador_extrato_periodo_data_efetiva_v1",
        layout_confidence=0.88,
        selected_parser="grouped",
        warning_count=2,
        balance_failed=2,
        bank_name="Caixa Economica Federal",
        bank_code="104",
    )

    visible = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(artifact.pdf_bytes)).pages)
    serialized = str(artifact.manifest)
    assert artifact.manifest["schema_version"] == "3"
    assert artifact.manifest["privacy_validation_version"] == "4"
    assert artifact.manifest["bank"]["code"] == "104"
    assert artifact.manifest["bank"]["name"] == "Caixa"
    assert artifact.manifest["layout_signals"]["privacy_mode"] == "semantic_transaction_v3"
    assert "03/11/2025" in visible
    assert "R$ 15,20 R$ 44.841,49 C" in visible
    assert "CRED PIX QR" in visible
    for forbidden in (
        "EMPRESA EXEMPLO LTDA",
        "12.345.678/0001-90",
        "MARIA DA SILVA",
        "987.654.321-00",
        "E00360305202511012219ABCDEF123456",
    ):
        assert forbidden not in visible
        assert forbidden not in serialized
    assert artifact.privacy_validated


def test_v3_canonical_pdf_remains_parseable_for_layout_diagnostics() -> None:
    source = _text_pdf(
        "GERENCIADOR CAIXA",
        "EMPRESA EXEMPLO LTDA",
        "CNPJ: 12.345.678/0001-90",
        "Agencia: 00001 Conta: 000000000001-0",
        "Saldo anterior ao periodo solicitado R$ 44.826,29 C",
        "Extrato no periodo de 01/11/2025 a 30/11/2025",
        "Data",
        "Data Efetiva",
        "Documento Historico Valor Saldo",
        "03/11/2025",
        "01/11 22:19",
        "012219 CRED PIX QR R$ 15,20 R$ 44.841,49 C",
        "03/11/2025",
        "03/11 11:57",
        "031157 DEB PIX CHAVE - R$ 140,00 R$ 44.701,49 C",
    )
    artifact = CanonicalLayoutGenerator(schema_version="3").generate(
        document=ingest_uploaded_document("statement.pdf", source),
        status="Revisao",
        conversion_type="pdf-ofx",
        transactions_count=2,
        layout_name="caixa_gerenciador_extrato_periodo_data_efetiva_v1",
        layout_confidence=0.88,
        selected_parser="grouped",
        warning_count=2,
        balance_failed=2,
        bank_name="Caixa Economica Federal",
        bank_code="104",
    )

    parsed = parse_pdf_transactions(artifact.pdf_bytes)

    assert parsed.layout.layout_name == "caixa_gerenciador_extrato_periodo_data_efetiva_v1"
    assert [transaction.amount for transaction in parsed.transactions] == [15.2, -140.0]
    assert [transaction.running_balance for transaction in parsed.canonical_transactions] == [44841.49, 44701.49]


def test_v3_uses_configured_page_limit_instead_of_v2_five_page_sample() -> None:
    writer = PdfWriter()
    for _ in range(7):
        writer.add_blank_page(width=595, height=842)
    output = BytesIO()
    writer.write(output)
    generator = CanonicalLayoutGenerator(schema_version="3", max_pages=20)

    artifact = generator.generate(
        document=ingest_uploaded_document("statement.pdf", output.getvalue()),
        status="Falha",
        conversion_type="pdf-ofx",
        transactions_count=0,
        layout_name=None,
        layout_confidence=None,
        selected_parser="ocr",
        warning_count=0,
        balance_failed=0,
        page_texts=tuple(f"PAGINA {index} DE 7" for index in range(1, 8)),
        page_text_source="ocr",
    )

    assert artifact.manifest["source_page_count"] == 7
    assert artifact.manifest["sampled_page_count"] == 7
    assert len(artifact.manifest["pages"]) == 7


@pytest.mark.parametrize(
    "unsafe_text",
    (
        "CLIENTE MARIA DA SILVA CPF 123.456.789-09",
        "AGENCIA 03726 CONTA 000574634316-8",
        "CONTATO maria@example.com",
    ),
)
def test_v3_privacy_validation_rejects_structured_pii(unsafe_text: str) -> None:
    with pytest.raises(ValueError, match="v3_structured_pii_detected"):
        validate_v3_output(source_text=unsafe_text, output_text=unsafe_text)
