"""Synthetic daily blocks; no customer document or extracted customer data."""

from io import BytesIO

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.application import pdf_parser as parser
from app.application.default_conversion_pipeline import (
    _resolve_closing_balance,
    _resolve_opening_balance,
    build_default_conversion_pipeline,
)
from app.application.models import TransactionRow

LAYOUT = "bradesco_empresas_negocios_extrato_lancamentos_v1"
HEADER = """Bradesco empresas e negócios
Extrato
Data e hora: 01/10/2026
Busca de lançamentos
Dados da empresa Agência Conta
Lançamentos
Data Descrição Documento Tipo Valor Saldo (R$)
"""


def statement_pages(*, rounded: bool = False) -> list[str]:
    end = "200,00" if rounded else "200,20"
    newest = "180,00" if rounded else "180,20"
    yield_amount = "0,01" if rounded else "0,20"
    return [
        HEADER
        + f"""30/09/2026 PAGAMENTO FICTÍCIO 100001 Débito -20,00 {newest}
30/09/2026 SALDO DO DIA {newest}
29/09/2026 RENTAB.INVEST FACILCRED* 100002 Crédito {yield_amount} {end}
29/09/2026 SALDO DO DIA {end}
29/09/2026 RENTAB.INVEST FACILCRED* 100003 Crédito 0,01 1.000,01
29/09/2026 PIX ENVIADO
DES: FORNECEDOR FICTÍCIO 29/09 100004 Débito -800,01 200,00
29/09/2026 SALDO DO DIA 200,00
""",
        HEADER
        + """28/09/2026 DEPÓSITO FICTÍCIO 100005 Crédito 1.000,00 1.000,00
28/09/2026 SALDO DO DIA 1.000,00
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
        commands = [b"BT /F1 10 Tf 12 TL 30 960 Td"]
        for line in text.splitlines():
            escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            commands.append(b"(" + escaped.encode("cp1252") + b") Tj T*")
        commands.append(b"ET")
        stream = DecodedStreamObject()
        stream.set_data(b"\n".join(commands))
        page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


@pytest.mark.parametrize("rounded", [False, True])
def test_split_same_day_is_ordered_by_balances_without_changing_financial_fields(rounded: bool) -> None:
    pages = statement_pages(rounded=rounded)
    result = parser._parse_pdf_transactions_from_page_texts(pages)
    assert result.layout.layout_name == LAYOUT
    assert result.parse_metrics["selected_parser"] == "layout_specific_modern_accounts"
    assert [row.external_reference_id for row in result.canonical_transactions] == ["100001", "100003", "100004", "100002", "100005"]
    assert [row.amount for row in result.transactions] == [-20, 0.01, -800.01, 0.01 if rounded else 0.20, 1000]
    assert [row.date for row in result.transactions] == ["2026-09-30", "2026-09-29", "2026-09-29", "2026-09-29", "2026-09-28"]
    assert result.parse_metrics["balance_consistency_checked"] == 2
    assert result.parse_metrics["balance_consistency_failed"] == 0
    assert [row.source_page for row in result.canonical_transactions] == [1, 1, 1, 1, 2]
    for row in result.canonical_transactions:
        source_line = pages[row.source_page - 1].splitlines()[row.source_line - 1]
        assert row.date[8:10] + "/" + row.date[5:7] + "/" + row.date[:4] in source_line
    assert "FORNECEDOR FICTÍCIO" in result.transactions[2].description


@pytest.mark.parametrize("mutation", ["no_boundary", "wrong_boundary_balance", "unreconciled_movement"])
def test_reordering_requires_a_printed_boundary_and_a_complete_valid_chain(mutation: str) -> None:
    pages = statement_pages()
    if mutation == "no_boundary":
        pages[0] = pages[0].replace("29/09/2026 SALDO DO DIA 200,20\n", "")
    elif mutation == "wrong_boundary_balance":
        pages[0] = pages[0].replace("29/09/2026 SALDO DO DIA 200,20", "29/09/2026 SALDO DO DIA 700,00")
    else:
        pages[0] = pages[0].replace("-800,01 200,00", "-800,01 202,00")
    result = parser._parse_pdf_transactions_from_page_texts(pages)
    assert [row.external_reference_id for row in result.canonical_transactions] == ["100001", "100002", "100003", "100004", "100005"]
    assert result.parse_metrics["balance_consistency_failed"] > 0


def test_already_valid_day_blocks_preserve_source_order() -> None:
    pages = statement_pages()
    pages[0] = pages[0].replace("Crédito 0,01 1.000,01", "Crédito 0,01 200,21").replace("-800,01 200,00", "-800,01 -599,80")
    result = parser._parse_pdf_transactions_from_page_texts(pages)
    assert [row.external_reference_id for row in result.canonical_transactions] == ["100001", "100002", "100003", "100004", "100005"]
    assert result.parse_metrics["balance_consistency_failed"] == 0


def test_ambiguous_daily_chain_is_not_reordered_or_deduplicated() -> None:
    pages = statement_pages()
    duplicate = "29/09/2026 RENTAB.INVEST FACILCRED* 100003 Crédito 0,01 1.000,01"
    pages[0] = pages[0].replace(duplicate, duplicate + "\n" + duplicate.replace("100003", "100006"))
    result = parser._parse_pdf_transactions_from_page_texts(pages)
    assert [row.external_reference_id for row in result.canonical_transactions] == [
        "100001",
        "100002",
        "100003",
        "100006",
        "100004",
        "100005",
    ]
    assert result.parse_metrics["balance_consistency_failed"] > 0


def test_split_day_can_continue_across_pdf_pages() -> None:
    pages = statement_pages()
    first, rest = pages[0].split("29/09/2026 RENTAB.INVEST FACILCRED* 100003", 1)
    pages = [first, HEADER + "29/09/2026 RENTAB.INVEST FACILCRED* 100003" + rest, pages[1]]
    result = parser._parse_pdf_transactions_from_page_texts(pages)
    assert [row.external_reference_id for row in result.canonical_transactions] == ["100001", "100003", "100004", "100002", "100005"]
    assert [row.source_page for row in result.canonical_transactions] == [1, 2, 2, 1, 3]
    assert result.parse_metrics["balance_consistency_failed"] == 0


@pytest.mark.parametrize("ascending", [False, True])
def test_period_balances_come_from_oldest_and_newest_days(ascending: bool) -> None:
    rows = [
        TransactionRow("2026-09-30", "NOVO", -20, "Outros", "unmatched", 80.20),
        TransactionRow("2026-09-29", "INTERMEDIÁRIO", -0.01, "Outros", "unmatched", 100.20),
        TransactionRow("2026-09-28", "ANTIGO", 1000, "Outros", "unmatched", 1000),
    ]
    if ascending:
        rows.reverse()
    assert _resolve_opening_balance(rows, layout_name=LAYOUT) == 0
    assert _resolve_closing_balance(rows, opening_balance=0, layout_name=LAYOUT) == 80.20


def test_period_balances_include_unbalanced_rows_at_edges_of_boundary_days() -> None:
    rows = [
        TransactionRow("2026-09-30", "NOVO", -20, "Outros", "unmatched", 80),
        TransactionRow("2026-09-30", "NOVO SEM SALDO", -5, "Outros", "unmatched"),
        TransactionRow("2026-09-28", "ANTIGO SEM SALDO", 10, "Outros", "unmatched"),
        TransactionRow("2026-09-28", "ANTIGO", 100, "Outros", "unmatched", 60),
    ]
    assert _resolve_opening_balance(rows, layout_name=LAYOUT) == -50
    assert _resolve_closing_balance(rows, opening_balance=-50, layout_name=LAYOUT) == 75


def test_period_metadata_does_not_borrow_balance_from_another_day() -> None:
    rows = [
        TransactionRow("2026-09-30", "NOVO SEM SALDO", -20, "Outros", "unmatched"),
        TransactionRow("2026-09-29", "INTERMEDIÁRIO", -10, "Outros", "unmatched", 100),
        TransactionRow("2026-09-28", "ANTIGO SEM SALDO", 50, "Outros", "unmatched"),
    ]
    assert _resolve_opening_balance(rows, layout_name=LAYOUT) is None
    assert _resolve_closing_balance(rows, layout_name=LAYOUT) is None


def test_explicit_opening_balance_remains_preferred_for_filtered_movements() -> None:
    rows = [TransactionRow("2026-09-28", "DEPÓSITO VISÍVEL", 1000, "Outros", "unmatched", 1000)]
    assert _resolve_opening_balance(rows, extracted_text="SALDO ANTERIOR -50,00", layout_name=LAYOUT) == -50


def test_native_pdf_pipeline_uses_correct_period_balances_without_ocr(monkeypatch) -> None:
    monkeypatch.setattr(parser, "is_textract_enabled", lambda: True)
    monkeypatch.setattr(parser, "is_textract_forced", lambda: False)
    monkeypatch.setattr(parser, "is_pdf_ocr_enabled", lambda: True)

    def unexpected(*args, **kwargs):
        pytest.fail("Native daily blocks must not invoke OCR")

    monkeypatch.setattr(parser, "_extract_pdf_page_texts_with_ocr", unexpected)
    monkeypatch.setattr(parser, "_parse_scanned_pdf_with_textract_gateway", unexpected)
    data = (
        build_default_conversion_pipeline()
        .run(filename="bradesco-ficticio.pdf", raw_bytes=native_pdf(statement_pages()), analysis_id="synthetic-daily-order")
        .analysis_data
    )
    assert data.transactions_total == 5
    assert data.opening_balance == 0
    assert data.closing_balance == 180.20
    assert data.pdf_processing_metrics["balance_consistency_failed"] == 0
    assert data.pdf_processing_metrics["textract_attempted"] == 0
