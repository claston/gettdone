"""Fictional account data reproducing signed opening balances between sections."""

from io import BytesIO

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.application import pdf_parser as pdf_parser_module
from app.application.default_conversion_pipeline import build_default_conversion_pipeline
from app.application.layout_profiles.registry import get_layout_profile
from app.application.pdf_parser import parse_pdf_transactions

LAYOUT = "bradesco_net_empresa_extrato_mensal_por_periodo_v1"
HEADER = "Data Lançamento Dcto. Crédito (R$) Débito (R$) Saldo (R$)"


def statement_pages(opening: str = "-2.500,00", *, latest_first_balance: str = "-2.512,50") -> list[str]:
    return [
        f"""Bradesco Net Empresa
Extrato Mensal / Por Período
Extrato de: Ag: 1234 | CC: 12345-6 | Entre 01/09/2026 e 30/09/2026
{HEADER}
31/08/2026 SALDO ANTERIOR 1.000,00
30/09/2026 DEPÓSITO FICTÍCIO 1001 100,00 1.100,00
TARIFA BANCARIA 1002 -20,00 1.080,00
Total 100,00 -20,00 1.080,00
Os dados acima têm como base o extrato mensal.
Últimos Lançamentos
{HEADER}
01/10/2026 SALDO ANTERIOR {opening}
01/10/2026 ENCARGOS LIMITE DE CRED 1003 -12,50 {latest_first_balance}
    CONTRATO FICTÍCIO
""",
        f"""Folha 2/2
Bradesco Net Empresa
{HEADER}
DEPÓSITO FICTÍCIO 1004 20,00 -2.492,50
Total 20,00 -12,50 -2.492,50
Saldos Invest Fácil / Plus
Data Histórico Valor (R$)
01/10/2026 SALDO INVEST FÁCIL 15,00
""",
    ]


def native_pdf(pages: list[str]) -> bytes:
    writer = PdfWriter()
    font = writer._add_object(
        DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Courier"),
                NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
            }
        )
    )
    for text in pages:
        page = writer.add_blank_page(width=850, height=1000)
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        instructions = [b"BT /F1 10 Tf 12 TL 30 960 Td"]
        for line in text.splitlines():
            escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            instructions.append(b"(" + escaped.encode("cp1252") + b") Tj T*")
        instructions.append(b"ET")
        stream = DecodedStreamObject()
        stream.set_data(b"\n".join(instructions))
        page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


@pytest.mark.parametrize("raw", ["-2.500,00", "-2500,00", "R$ -2.500,00", "-R$ 2.500,00", "−2.500,00", "- 2.500,00"])
def test_opening_balance_keeps_negative_sign_when_profile_captures_sign_as_role(raw: str) -> None:
    lines = [pdf_parser_module._PdfLine(text=f"01/10/2026 SALDO ANTERIOR {raw}", page_number=1, line_number=1)]
    balance = pdf_parser_module._resolve_tabular_opening_running_balance(
        lines=lines,
        anchor_index=0,
        tabular_profile=get_layout_profile(LAYOUT),
    )
    assert balance == -2500


@pytest.mark.parametrize("opening", ["-2.500,00", "-2500,00", "R$ -2.500,00", "\n-2.500,00"])
def test_negative_latest_section_opening_does_not_create_false_balance_warning(opening: str) -> None:
    result = pdf_parser_module._parse_pdf_transactions_from_page_texts(statement_pages(opening), preserve_layout_spacing=True)
    assert result.layout.layout_name == LAYOUT
    assert result.parse_metrics["selected_parser"] == "layout_specific_bradesco_net_empresa"
    assert [row.amount for row in result.transactions] == [100, -20, -12.50, 20]
    assert [row.date for row in result.transactions] == ["2026-09-30", "2026-09-30", "2026-10-01", "2026-10-01"]
    assert [row.running_balance for row in result.canonical_transactions] == [1100, 1080, -2512.50, -2492.50]
    assert [row.source_page for row in result.canonical_transactions] == [1, 1, 1, 2]
    assert result.transactions[2].description.endswith("CONTRATO FICTÍCIO")
    assert result.parse_metrics["balance_consistency_checked"] == 3
    assert result.parse_metrics["balance_consistency_failed"] == 0
    assert all("balance_consistency_failed" not in row.warnings for row in result.canonical_transactions)


@pytest.mark.parametrize("latest_first_balance", ["-2.511,50", "-2.513,50"])
def test_actual_discrepancy_after_negative_opening_is_still_reported(latest_first_balance: str) -> None:
    result = pdf_parser_module._parse_pdf_transactions_from_page_texts(statement_pages(latest_first_balance=latest_first_balance))
    assert "balance_consistency_failed" in result.canonical_transactions[2].warnings
    assert result.parse_metrics["balance_consistency_failed"] == 2


def test_later_real_discrepancy_in_latest_section_remains_visible() -> None:
    pages = statement_pages()
    pages[1] = pages[1].replace("20,00 -2.492,50", "20,00 -2.491,50")
    result = pdf_parser_module._parse_pdf_transactions_from_page_texts(pages)
    assert result.parse_metrics["balance_consistency_checked"] == 3
    assert result.parse_metrics["balance_consistency_failed"] == 1
    assert "balance_consistency_failed" not in result.canonical_transactions[2].warnings
    assert "balance_consistency_failed" in result.canonical_transactions[3].warnings


def test_native_pdf_and_pipeline_preserve_transactions_and_closing_balance_without_ocr(monkeypatch) -> None:
    monkeypatch.setattr(pdf_parser_module, "is_textract_enabled", lambda: True)
    monkeypatch.setattr(pdf_parser_module, "is_textract_forced", lambda: False)
    monkeypatch.setattr(pdf_parser_module, "is_pdf_ocr_enabled", lambda: True)

    def unexpected_ocr(*args, **kwargs):
        pytest.fail("Native Bradesco statement must not invoke OCR")

    monkeypatch.setattr(pdf_parser_module, "_extract_pdf_page_texts_with_ocr", unexpected_ocr)
    monkeypatch.setattr(pdf_parser_module, "_parse_scanned_pdf_with_textract_gateway", unexpected_ocr)
    raw = native_pdf(statement_pages())
    parsed = parse_pdf_transactions(raw)
    assert len(parsed.transactions) == 4
    assert parsed.parse_metrics["balance_consistency_failed"] == 0
    assert parsed.parse_metrics["textract_attempted"] == 0
    data = (
        build_default_conversion_pipeline()
        .run(filename="bradesco-sintetico.pdf", raw_bytes=raw, analysis_id="synthetic-bradesco")
        .analysis_data
    )
    assert data.transactions_total == 4
    assert data.opening_balance == 1000
    assert data.closing_balance == -2492.50
    assert all("balance_consistency_failed" not in row.warning_types for row in data.preview_transactions)
