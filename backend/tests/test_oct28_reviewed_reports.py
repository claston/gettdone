"""Financial source evidence, not historical parser counts, defines the rows."""

from decimal import Decimal

import pytest

from app.application.errors import PdfFinancialValidationError
from app.application.financial_document_export import write_financial_artifacts
from app.application.financial_event_projection import parse_financial_pdf_pages
from app.application.parsers.pdf.omie_cash import parse_omie_cash
from app.application.parsers.pdf.reviewed_native_statements import _cards, _payments
from tests.test_recovery_sweep_ocr import extraction


def omie_pages():
    return [
        "\n".join(
            [
                "TESTE - Finanças - Movimentação da Conta Corrente",
                "Situação Data Cliente Conta Corrente Valor (R$) Saldo (R$) Saldo Previsto (R$)",
                "31/08/2026 SALDO ANTERIOR 0.00 100.00 100.00",
                "Conciliado (bloqueado) 01/09/2026 Cliente Omie.CASH Serviço 50.00 150.00 150.00 Conta Recebida",
                "Boleto com vencimento em 25/08/2026; juros R$ 0,00",
                "Atrasado 01/09/2026 Cliente pendente Omie.CASH Projeto 90.00 150.00 240.00 Previsão",
                "Conciliado (bloqueado) 02/09/2026 Fornecedor Omie.CASH Despesa -20.00 130.00 220.00 Conta Paga",
                "02/09/2026 SALDO 0.00 130.00 220.00",
            ]
        )
    ]


def test_omie_separates_realized_cash_pending_and_controls(tmp_path):
    result = parse_financial_pdf_pages(omie_pages(), native=True)
    document = result.financial_document
    assert [t.date for t in result.transactions] == ["2026-09-01", "2026-09-01", "2026-09-02"]
    assert [t.type for t in result.transactions] == ["inflow", "non_cash", "outflow"]
    assert [e["cash_effect"] for e in document["events"]] == ["50.00", None, "-20.00"]
    assert all(v["status"] == "passed" for v in document["validations"])
    assert Decimal(document["metadata"]["closing_balance"]) == 130
    write_financial_artifacts(tmp_path, document, bank_code="001", bank_branch="0001", account_number="12345")
    ofx = (tmp_path / "converted.ofx").read_text(encoding="utf-8")
    assert ofx.count("<STMTTRN>") == 2
    assert "<TRNAMT>-20.00" in ofx
    assert "90.00" not in ofx
    assert "Cliente pendente" in (tmp_path / "converted.csv").read_text(encoding="utf-8-sig")


def test_omie_disagreement_remains_visible_and_disables_ofx():
    pages = [omie_pages()[0].replace("-20.00 130.00 220.00", "-20.00 131.00 220.00")]
    document = parse_omie_cash(pages)
    assert any(v["status"] == "failed" for v in document["validations"])
    assert document["export"]["available_formats"] == ["csv", "xlsx"]


def test_wrapped_payment_beneficiary_keeps_first_and_last_lines():
    source = extraction(
        [
            ("FABRICA DE", 0.086, 0.195),
            ("PRODUTOS TESTE", 0.086, 0.204),
            ("01/09/2026", 0.612, 0.200),
            ("50,00 efetuado", 0.77, 0.200),
            ("OUTRO BENEFICIÁRIO", 0.086, 0.224),
            ("02/09/2026", 0.612, 0.224),
            ("20,00 efetuado", 0.77, 0.224),
        ]
    )
    statement = _payments(source)
    assert [r.transaction.description for r in statement.rows] == ["FABRICA DE PRODUTOS TESTE", "OUTRO BENEFICIÁRIO"]
    assert [r.transaction.amount for r in statement.rows] == [-50, -20]
    assert len(statement.rows[0].source_line_refs) == 4


def test_payment_name_recovery_also_preserves_identifiers_and_payment_type():
    source = extraction(
        [
            ("FABRICA DE", 0.086, 0.195),
            ("PRODUTOS TESTE", 0.086, 0.204),
            ("00.000.000/0001-00", 0.233, 0.200),
            ("Boleto outros", 0.351, 0.195),
            ("bancos", 0.351, 0.204),
            ("00001234", 0.469, 0.200),
            ("01/09/2026", 0.612, 0.200),
            ("50,00 efetuado", 0.77, 0.200),
        ]
    )
    statement = _payments(source)
    row = statement.rows[0]
    assert row.transaction.description == "FABRICA DE PRODUTOS TESTE 00.000.000/0001-00 Boleto outros bancos 00001234"
    assert row.external_reference_id == "00001234"
    assert len(row.source_line_refs) == 8


def test_dated_cards_preserve_all_cash_rows_and_validate_every_daily_balance():
    source = extraction(
        [
            ("22/04/2025", 0.495, 0.20),
            ("saldo do dia", 0.186, 0.23),
            ("R$ 100,00", 0.742, 0.23),
            ("23/04/2025", 0.495, 0.28),
            ("PIX 22/04 é descrição", 0.186, 0.32),
            ("R$ 50,00", 0.742, 0.325),
            ("TARIFA", 0.186, 0.36),
            ("- R$ 20,00", 0.742, 0.365),
            ("SALDO TOTAL DISPONÍVEL DIA", 0.186, 0.4),
            ("R$ 130,00", 0.742, 0.405),
        ]
    )
    statement = _cards(source)
    assert [r.transaction.amount for r in statement.rows] == [50, -20]
    assert {r.transaction.date for r in statement.rows} == {"2025-04-23"}
    assert statement.printed_balance_checks == 2
    assert statement.closing == 130


def test_dated_cards_do_not_adjust_values_to_fit_a_balance():
    source = extraction(
        [
            ("22/04/2025", 0.495, 0.20),
            ("saldo do dia", 0.186, 0.23),
            ("R$ 100,00", 0.742, 0.23),
            ("PIX TESTE", 0.186, 0.32),
            ("R$ 50,00", 0.742, 0.325),
            ("SALDO TOTAL DISPONÍVEL DIA", 0.186, 0.4),
            ("R$ 151,00", 0.742, 0.405),
        ]
    )
    with pytest.raises(PdfFinancialValidationError):
        _cards(source)


def bradesco_unified_source(*, savings=False):
    from dataclasses import replace

    from app.application.document_extraction_models import RawDocumentExtraction

    def table(product):
        doc_x = 0.38 if product == "savings" else 0.47
        cells = [
            ("Data", 0.1, 0.1),
            ("Histórico", 0.27, 0.1),
            ("Documento", doc_x, 0.1),
            ("Crédito", 0.6, 0.1),
            ("Débito", 0.73, 0.1),
            ("Saldo", 0.85, 0.1),
            ("31/08", 0.1, 0.14),
            ("Saldo Anterior", 0.14, 0.14),
            ("100,00", 0.88, 0.14),
            ("01/09", 0.1, 0.2),
            ("PIX RECEBIDO", 0.14, 0.2),
            ("0001234", doc_x, 0.2),
            ("50,00", 0.62, 0.2),
            ("PIX ENVIADO", 0.14, 0.3),
            ("0001235", doc_x, 0.3),
            ("20,00", 0.75, 0.3),
            ("130,00", 0.88, 0.33),
            ("Total da Movimentação", 0.14, 0.4),
            ("50,00", 0.62, 0.4),
            ("20,00", 0.75, 0.4),
            ("130,00", 0.88, 0.4),
        ]
        if product == "savings":
            cells.append(("Índices", 0.49, 0.1))
        else:
            cells.extend([("Bradesco Extrato Unificado Pessoa Jurídica", 0.1, 0.02), ("01/09/2026 a 30/09/2026", 0.1, 0.05)])
        return extraction(cells).pages[0]

    pages = [table("checking")]
    if savings:
        page = table("savings")
        pages.append(replace(page, page_number=2, lines=[replace(line, id="page2_" + line.id, page_number=2) for line in page.lines]))
    return RawDocumentExtraction("aws_textract", "fictional", pages, {})


@pytest.mark.parametrize("savings,count,ofx", [(False, 2, True), (True, 4, False)])
def test_bradesco_keeps_accounts_separate_and_does_not_export_balances(savings, count, ofx):
    from app.application.parsers.pdf.bradesco_unified_ocr import parse_bradesco_unified

    document = parse_bradesco_unified(bradesco_unified_source(savings=savings))
    assert len(document["events"]) == count
    assert [Decimal(e["cash_effect"]) for e in document["events"]].count(Decimal(50)) == count // 2
    assert all(v["status"] == "passed" for v in document["validations"])
    assert ("ofx" in document["export"]["available_formats"]) == ofx
    assert len(document["observations"]) == count // 2


def test_bad_embedded_money_glyphs_require_visual_ocr_without_guessing_values():
    from app.application.native_text_quality import assess_native_text_quality
    from tests.test_embedded_ocr_quality import layered_pdf

    text = "Extrato banco teste\n02/02/2026 PIX i506,95 1.000,00\n03/02/2026 PIX •420,00 580,00"
    raw = layered_pdf(text)
    result = assess_native_text_quality(raw, [text])
    assert result.requires_visual_extraction
    assert "malformed_money_glyphs" in result.evidence
    assert not assess_native_text_quality(layered_pdf(text, raster=False), [text]).requires_visual_extraction


def test_unified_ocr_keeps_two_months_in_the_same_product():
    from dataclasses import replace

    from app.application.parsers.pdf.bradesco_unified_ocr import parse_bradesco_unified

    source = bradesco_unified_source()
    page = source.pages[0]
    lines = []
    for line in page.lines:
        text = line.text.replace("01/09/2026 a 30/09/2026", "01/10/2026 a 31/10/2026").replace("01/09", "01/10").replace("31/08", "30/09")
        if line.bbox["top"] == 0.14 and text == "100,00":
            text = "130,00"
        elif text == "130,00":
            text = "160,00"
        lines.append(replace(line, id="month2_" + line.id, page_number=2, text=text))
    cover = replace(page, lines=[replace(page.lines[0], id="cover", text="Extratos mensais", bbox={**page.lines[0].bbox, "top": 0.02})])
    pages = [cover, page, replace(page, page_number=2, lines=lines)]
    source = replace(
        source,
        pages=[
            replace(item, page_number=index, lines=[replace(line, id=f"scope{index}_" + line.id, page_number=index) for line in item.lines])
            for index, item in enumerate(pages, 1)
        ],
    )
    document = parse_bradesco_unified(source)
    assert [e["dates"]["transaction"] for e in document["events"]] == ["2026-09-01", "2026-09-01", "2026-10-01", "2026-10-01"]
    assert all(v["status"] == "passed" for v in document["validations"])
    assert len(document["observations"]) == 2


@pytest.mark.parametrize("missing", ["opening", "closing"])
def test_unified_ocr_preserves_readable_rows_when_a_control_is_missing(missing):
    from dataclasses import replace

    from app.application.parsers.pdf.bradesco_unified_ocr import parse_bradesco_unified

    source = bradesco_unified_source()
    page = source.pages[0]
    lines = [line for line in page.lines if line.bbox["top"] != (0.14 if missing == "opening" else 0.4)]
    document = parse_bradesco_unified(replace(source, pages=[replace(page, lines=lines)]))
    assert [Decimal(e["cash_effect"]) for e in document["events"]] == [50, -20]
    assert any(v["status"] == "failed" for v in document["validations"])
    assert document["export"]["available_formats"] == ["csv", "xlsx"]
