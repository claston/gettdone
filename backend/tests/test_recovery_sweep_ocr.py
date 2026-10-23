"""OCR geometry fixtures with fictional bank rows and account holders."""

from dataclasses import replace

import pytest

from app.application import pdf_parser
from app.application.document_extraction_models import ExtractedLine, ExtractedPage, RawDocumentExtraction
from app.application.errors import InvalidFileContentError, PdfFinancialValidationError
from app.application.sweep_statement_detection import INFINITEPAY, identify_sweep_statement


@pytest.mark.parametrize("domain", ["infinitepay.io", "ajuda@infinitepay.io", "https://www.infinitepay.io/ajuda"])
def test_infinitepay_identity_requires_complete_domain(domain):
    text = f"{domain}\nTipo de transação Detalhe Valor (R$)"
    assert identify_sweep_statement(text) == INFINITEPAY


@pytest.mark.parametrize("domain", ["infinitepay.io.example.org", "notinfinitepay.io", "other.infinitepay.io"])
def test_infinitepay_identity_rejects_similar_domains(domain):
    text = f"{domain}\nTipo de transação Detalhe Valor (R$)"
    assert identify_sweep_statement(text) is None


def test_itau_scanned_monthly_without_readable_movements_keeps_ocr_evidence():
    source = extraction(
        [
            ("Conta Corrente e Aplicações Automáticas", 0.1, 0.03),
            ("data descrição entradas R$ saídas R$ saldo R$", 0.1, 0.1),
            ("SALDO APLIC AUT MAIS", 0.2, 0.4),
            ("extrato mensal jun 2026 001/006", 0.1, 0.02),
        ]
    )
    with pytest.raises(InvalidFileContentError) as error:
        pdf_parser._parse_textract_text_pages(source)
    assert error.value._parse_observability["layout_inference_name"].startswith("itau_empresas_extrato_mensal_")
    assert error.value._ocr_source_extraction is source


def test_bb_ocr_keeps_split_debit_sign_and_decimal_dot_without_using_document_as_amount():
    source = extraction(
        [
            ("Cliente Conta atual Dt. balancete Dt. movimento Ag. origem", 0.1, 0.02),
            ("Histórico", 0.35, 0.1),
            ("Documento", 0.65, 0.1),
            ("Valor R$", 0.75, 0.1),
            ("Saldo", 0.86, 0.1),
            ("30/06/2026", 0.1, 0.2),
            ("000 Saldo Anterior", 0.35, 0.2),
            ("100,00 C", 0.84, 0.2),
            ("01/07/2026", 0.1, 0.3),
            ("821 Pix Recebido", 0.35, 0.3),
            ("123.001", 0.65, 0.3),
            ("50.00 C", 0.75, 0.3),
            ("150,00 C", 0.84, 0.3),
            ("02/07/2026", 0.1, 0.4),
            ("144 Pix Enviado", 0.35, 0.4),
            ("123.002", 0.65, 0.4),
            ("20,00 D", 0.75, 0.4),
            ("130,00 C", 0.84, 0.4),
            ("31/07/2026", 0.1, 0.5),
            ("999 S A L D O", 0.35, 0.5),
            ("130,00 C", 0.84, 0.5),
        ]
    )
    result = pdf_parser._parse_textract_text_pages(source)
    assert [t.amount for t in result.transactions] == [50, -20]
    assert result.parse_metrics["verified_opening_balance"] == 100
    assert result.parse_metrics["verified_closing_balance"] == 130
    lines = [replace(item, text="21,00 D") if item.text == "20,00 D" else item for item in source.pages[0].lines]
    with pytest.raises(PdfFinancialValidationError):
        pdf_parser._parse_textract_text_pages(replace(source, pages=[replace(source.pages[0], lines=lines)]))


def test_gateway_table_validation_failure_keeps_successful_ocr_evidence(monkeypatch):
    from types import SimpleNamespace

    from app.application.errors import UnsupportedDocumentContentError

    source = extraction([("trecho sem cabeçalho", 0.1, 0.1)])
    monkeypatch.setattr(
        pdf_parser,
        "TextractGateway",
        lambda: SimpleNamespace(
            analyze_pdf=lambda **kwargs: {"metrics": {"textract_mode": "tables"}},
        ),
    )
    monkeypatch.setattr(pdf_parser, "map_textract_blocks_to_extraction", lambda **kwargs: source)

    def reject(*args):
        raise UnsupportedDocumentContentError(document_type="unknown", message="Documento sem identificação suficiente.")

    monkeypatch.setattr(pdf_parser, "_parse_textract_tables", reject)
    with pytest.raises(UnsupportedDocumentContentError) as error:
        pdf_parser._parse_scanned_pdf_with_textract_gateway(b"synthetic")
    assert error.value._ocr_source_extraction is source
    assert error.value._parse_observability["textract_used"] == 1
    assert error.value._parse_observability["ocr_result_received"] == 1


def extraction(cells, *, page=1):
    return RawDocumentExtraction(
        provider="aws_textract",
        document_hash="synthetic",
        metrics={},
        pages=[
            ExtractedPage(
                page_number=page,
                lines=[
                    ExtractedLine(
                        id=f"line_{n}",
                        page_number=page,
                        line_index=n,
                        text=text,
                        confidence=99,
                        bbox={"left": x, "top": y, "width": 0.07, "height": 0.01},
                    )
                    for n, (text, x, y) in enumerate(cells, 1)
                ],
            )
        ],
    )


def infinitepay():
    return extraction(
        [
            ("Relatório de movimentações InfinitePay", 0.05, 0.04),
            ("ajuda@infinitepay.io", 0.05, 0.95),
            ("01 Jan, 2026 24 Jun, 2026", 0.05, 0.08),
            ("Data", 0.05, 0.15),
            ("Hora", 0.15, 0.15),
            ("Tipo de transação", 0.25, 0.15),
            ("Nome", 0.4, 0.15),
            ("Detalhe", 0.65, 0.15),
            ("Valor (R$)", 0.85, 0.15),
            ("02 Jan, 2026", 0.05, 0.2),
            ("10:00", 0.15, 0.2),
            ("Pix", 0.25, 0.2),
            ("COMERCIO TESTE", 0.4, 0.2),
            ("Recebido", 0.65, 0.2),
            ("+50,00", 0.85, 0.2),
            ("11:00", 0.15, 0.3),
            ("Pix", 0.25, 0.3),
            ("DESTINO TESTE", 0.4, 0.3),
            ("Enviado", 0.65, 0.3),
            ("-20,00", 0.85, 0.3),
            ("Página 11 de 23", 0.7, 0.95),
        ]
    )


def test_sicoob_descending_cd_values_use_daily_controls_and_opening():
    source = extraction(
        [
            ("SICOOB SISBR EXTRATO DE CONTA CORRENTE", 0.1, 0.02),
            ("Periodo: 01/12/2025 31/12/2025", 0.1, 0.06),
            ("Data", 0.05, 0.1),
            ("Documento", 0.15, 0.1),
            ("Histórico", 0.3, 0.1),
            ("Valor", 0.9, 0.1),
            ("02/12", 0.05, 0.2),
            ("129", 0.15, 0.2),
            ("DÉBITO TESTE", 0.3, 0.2),
            ("R$ 20,00D", 0.88, 0.2),
            ("SALDO DO DIA", 0.3, 0.25),
            ("R$ 130,00C", 0.88, 0.25),
            ("01/12", 0.05, 0.3),
            ("Pix", 0.15, 0.3),
            ("PIX TESTE", 0.3, 0.3),
            ("R$ 50,00C", 0.88, 0.3),
            ("SALDO DO DIA", 0.3, 0.35),
            ("R$ 150,00C", 0.88, 0.35),
            ("30/11", 0.05, 0.4),
            ("SALDO ANTERIOR", 0.3, 0.4),
            ("R$ 100,00C", 0.88, 0.4),
            ("RESUMO", 0.3, 0.5),
            ("Saldo em conta: 130,00C", 0.3, 0.55),
        ]
    )
    result = pdf_parser._parse_textract_text_pages(source)
    assert [t.amount for t in result.transactions] == [50, -20]
    assert result.parse_metrics["verified_opening_balance"] == 100
    assert result.parse_metrics["verified_closing_balance"] == 130
    lines = [replace(item, text="R$ 21,00D") if item.text == "R$ 20,00D" else item for item in source.pages[0].lines]
    with pytest.raises(PdfFinancialValidationError):
        pdf_parser._parse_textract_text_pages(replace(source, pages=[replace(source.pages[0], lines=lines)]))


def test_bradesco_latest_section_does_not_duplicate_main_period_movements():
    from tests.test_oct20_followup_ocr import bradesco

    source = bradesco()
    second = source.pages[1]
    extra = (
        extraction(
            [
                ("Total", 0.19, 0.4),
                ("50,00", 0.58, 0.4),
                ("-20,00", 0.70, 0.4),
                ("130,00", 0.83, 0.4),
                ("Últimos Lançamentos", 0.19, 0.5),
                ("SALDO ANTERIOR", 0.19, 0.6),
                ("130,00", 0.83, 0.6),
                ("05/05/2026", 0.1, 0.7),
                ("PIX TESTE", 0.19, 0.7),
                ("000003", 0.44, 0.7),
                ("30,00", 0.58, 0.7),
                ("160,00", 0.83, 0.7),
            ],
            page=2,
        )
        .pages[0]
        .lines
    )
    source = replace(source, pages=[source.pages[0], replace(second, lines=[*second.lines, *extra])])
    result = pdf_parser._parse_textract_text_pages(source)
    assert [t.amount for t in result.transactions] == [50, -20]
    assert result.parse_metrics["verified_closing_balance"] == 130
    bad = [replace(item, text="51,00") if item is extra[1] else item for item in source.pages[1].lines]
    with pytest.raises(PdfFinancialValidationError):
        pdf_parser._parse_textract_text_pages(replace(source, pages=[source.pages[0], replace(second, lines=bad)]))


def pagbank():
    cells = [
        ("290 PagSeguro Internet Instituição de Pagamento S/A PagBank", 0.1, 0.02),
        ("Extrato Financeiro", 0.1, 0.05),
        ("Periodo: 01/05/2025 a 31/05/2025", 0.1, 0.08),
        ("Data", 0.03, 0.1),
        ("Código da transação", 0.19, 0.1),
        ("Descrição", 0.53, 0.1),
        ("Conta", 0.75, 0.1),
        ("Valor(R$)", 0.86, 0.1),
    ]
    for date, ref, description, account, value, y in [
        ("02/05/2025 10:00:00", "REF-02", "Pagamento liberado", "A receber", "-30,00", 0.2),
        ("02/05/2025 09:00:00", "REF-02", "Pagamento liberado", "Disponível", "30,00", 0.25),
        ("02/05/2025 09:00:00", "REF-02", "Liberação de saldo em conta corrente", "Disponível", "-30,00", 0.3),
        ("01/05/2025 10:00:00", "REF-01", "Venda pela Moderninha", "A receber", "100,00", 0.6),
    ]:
        cells += [(date, 0.03, y), (ref, 0.19, y), (description, 0.53, y), (account, 0.75, y), (value, 0.86, y)]
    for date, receivable, y in [("02/05/2025", "70,00", 0.4), ("01/05/2025", "100,00", 0.7)]:
        cells += [
            (f"Data: {date}", 0.03, y),
            ("SALDO DO DIA Disponível: 0,00", 0.53, y + 0.02),
            (f"A receber: {receivable}", 0.53, y + 0.04),
            ("Bloqueado: 0,00", 0.53, y + 0.06),
        ]
    return extraction(cells)


def test_pagbank_preserves_available_and_receivable_accounts_without_mixing_cash():
    result = pdf_parser._parse_textract_text_pages(pagbank())
    document = result.financial_document
    assert document["document_type"] == "payment_account_financial_report"
    assert len(document["events"]) == 4
    assert len(document["observations"]) == 2
    assert {e["attributes"]["subaccount"] for e in document["events"]} == {"available", "receivables"}
    assert sum(float(e["cash_effect"] or 0) for e in document["events"]) == 0
    assert len([e for e in document["events"] if e["cash_effect"] is None]) == 2
    assert document["export"]["available_formats"] == ["csv", "xlsx"]
    assert all(v["status"] == "passed" for v in document["validations"])
    assert all(set(event["dates"]) == {"transaction"} for event in document["events"])


@pytest.mark.parametrize("rotated", [False, True])
def test_infinitepay_signed_rows_in_partial_report_keep_dates_and_geometry(rotated):
    source = infinitepay()
    if rotated:
        lines = [
            replace(
                line,
                bbox={
                    "left": line.bbox["top"],
                    "top": 1 - line.bbox["left"] - line.bbox["width"],
                    "width": line.bbox["height"],
                    "height": line.bbox["width"],
                },
            )
            for line in source.pages[0].lines
        ]
        source = replace(source, pages=[replace(source.pages[0], lines=lines)])
    result = pdf_parser._parse_textract_text_pages(source)
    assert result.layout.layout_name == INFINITEPAY
    assert [t.amount for t in result.transactions] == [50, -20]
    assert {t.date for t in result.transactions} == {"2026-01-02"}
    assert result.parse_metrics["partial_statement"] == 1
    assert result.parse_metrics["financial_controls_validated"] == 0
    assert all(t.source_regions for t in result.canonical_transactions)


@pytest.mark.parametrize("mutation", ["sign", "date", "orphan", "time", "period", "malformed_date"])
def test_infinitepay_cannot_guess_missing_dates_signs_or_unpaired_values(mutation):
    source = infinitepay()
    lines = list(source.pages[0].lines)
    if mutation == "sign":
        lines = [replace(item, text="50,00") if item.text == "+50,00" else item for item in lines]
    elif mutation == "date":
        lines = [replace(item, text="31 Fev, 2026") if item.text == "02 Jan, 2026" else item for item in lines]
    elif mutation == "time":
        lines = [replace(item, text="99:99") if item.text == "10:00" else item for item in lines]
    elif mutation == "period":
        lines = [replace(item, text="02 Jul, 2026") if item.text == "02 Jan, 2026" else item for item in lines]
    elif mutation == "malformed_date":
        lines.append(replace(lines[9], id="bad-date", line_index=50, text="03 Jax, 2026", bbox={**lines[9].bbox, "top": 0.3}))
    else:
        lines.append(replace(lines[-2], id="orphan", line_index=50, bbox={**lines[-2].bbox, "top": 0.5}))
    with pytest.raises(PdfFinancialValidationError):
        pdf_parser._parse_textract_text_pages(replace(source, pages=[replace(source.pages[0], lines=lines)]))
