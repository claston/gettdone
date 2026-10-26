"""Fictional reproductions of the second Oct 26 recovery snapshot."""
from dataclasses import replace

import pytest

from app.application import pdf_parser
from app.application.document_extraction_models import ExtractedWord
from app.application.errors import InvalidFileContentError, PdfFinancialValidationError
from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf
from tests.fixtures.recovery_acceptance import grid_pdf
from tests.test_oct20_followup_native import portal_pages
from tests.test_oct20_new_native_statements import bb_page
from tests.test_recovery_case_ocr import bb_source
from tests.test_recovery_sweep_ocr import extraction


def stone_empty_lines():
    return ["Stone Instituição de Pagamento S.A.", "Extrato de conta corrente",
            "Titular: EMPRESA FICTICIA", "Agência 0001 Conta 123456-7",
            "Período 01/02/2025 até 28/02/2025",
            "DATA TIPO LANÇAMENTO VALOR (R$) SALDO (R$) CONTRAPARTE",
            "Não há dados a serem exibidos", "Informações do Comprovante"]


def test_stone_explicitly_empty_statement_is_not_an_extraction_failure(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    raw = generate_native_text_pdf((tuple(stone_empty_lines()),))
    result = pdf_parser.parse_pdf_transactions(raw)
    assert result.transactions == []
    assert result.parse_metrics["statement_no_activity"] == 1
    assert result.parse_metrics["selected_parser"] == "explicit_no_activity"


@pytest.mark.parametrize("mutation", ["activity", "unknown", "no_declaration"])
def test_stone_empty_detection_requires_an_inactive_known_statement(mutation):
    lines = stone_empty_lines()
    if mutation == "activity":
        lines.append("03/02/2025 PIX RECEBIDO 10,00 C 10,00 C")
    elif mutation == "unknown":
        lines[0] = "Instituição não identificada"
    else:
        lines.pop(-2)
    try:
        result = pdf_parser._parse_pdf_transactions_from_page_texts(["\n".join(lines)])
    except InvalidFileContentError:
        return
    assert not result.parse_metrics.get("statement_no_activity")


@pytest.mark.parametrize("period", ["01/08/2026 até 31/08/2026", "01/08/2026 até 30/09/2026"])
def test_banrisul_portal_accepts_full_printed_period_without_relaxing_controls(period):
    pages = [page.replace("PERIODO: 08/2026", "PERIODO: " + period) for page in portal_pages()]
    result = pdf_parser._parse_pdf_transactions_from_page_texts(pages)
    assert [row.amount for row in result.transactions] == [150, -20, -50]
    assert result.parse_metrics["printed_balance_checks"] == 2
    assert result.parse_metrics["verified_opening_balance"] == -100
    assert result.parse_metrics["verified_closing_balance"] == -20


@pytest.mark.parametrize("stroke", ["I", "l", "|"])
def test_bb_table_stroke_before_a_separate_explicit_sign_is_not_an_amount_digit(stroke):
    source = bb_source()
    page = source.pages[0]
    lines = [replace(line, text=f"20,00 {stroke}") if line.text == "20,00 D" else line for line in page.lines]
    lines += extraction([("D", .831, .3)]).pages[0].lines
    result = pdf_parser._parse_textract_text_pages(replace(source, pages=[replace(page, lines=lines)]))
    assert [row.amount for row in result.transactions] == [-20]
    assert result.parse_metrics["financial_controls_validated"] == 1


def test_bb_table_stroke_without_a_sign_cannot_be_silently_repaired():
    source = bb_source()
    page = source.pages[0]
    lines = [replace(line, text="20,00 I") if line.text == "20,00 D" else line for line in page.lines]
    with pytest.raises(PdfFinancialValidationError):
        pdf_parser._parse_textract_text_pages(replace(source, pages=[replace(page, lines=lines)]))


def ndd_native(*, count="3", last_amount="10,00"):
    return grid_pdf([[
        ("NDD IP Conta Digital | Extrato", .03, .03),
        ("De: 01/10/2026 Até: 02/10/2026", .03, .07),
        ("Saldo inicial 01/10:", .68, .10), ("R$-", .93, .10),
        ("Id", .03, .16), ("Data/Hora", .13, .16), ("Lançamento", .3, .16),
        ("Operação", .43, .16), ("Descrição da Movimentação", .56, .16), ("Valor", .93, .16),
        ("10003", .03, .23), ("02/10/2026 12:00", .13, .23), ("Débito", .3, .23),
        ("PIX", .43, .23), ("PAGAMENTO FICTICIO", .56, .23), (last_amount, .93, .23),
        ("Saldo final do dia 02/10/2026:", .68, .28), ("R$60,00", .93, .28),
        ("10002", .03, .36), ("01/10/2026 11:00", .13, .36), ("Débito", .3, .36),
        ("PIX", .43, .36), ("PAGAMENTO FICTICIO", .56, .36), ("30,00", .93, .36),
        ("10001", .03, .42), ("01/10/2026 10:00", .13, .42), ("Crédito", .3, .42),
        ("PIX", .43, .42), ("RECEBIMENTO FICTICIO", .56, .42), ("100,00", .93, .42),
        ("Saldo final do dia 01/10/2026:", .68, .48), ("R$70,00", .93, .48),
        ("Total geral:", .68, .56), ("60,00", .93, .56),
        ("Total Crédito:", .68, .60), ("100,00", .93, .60),
        ("Total Débito:", .68, .64), ("40,00", .93, .64),
        ("Quantidade de Operações:", .68, .68), (count, .93, .68),
    ]])


def test_ndd_native_id_before_datetime_preserves_all_directions_and_totals(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    result = pdf_parser.parse_pdf_transactions(ndd_native())
    assert [row.amount for row in result.transactions] == [100, -30, -10]
    assert result.parse_metrics["printed_balance_checks"] >= 2
    assert result.parse_metrics["verified_opening_balance"] == 0
    assert result.parse_metrics["verified_closing_balance"] == 60
    assert all(row.source_regions for row in result.canonical_transactions)


@pytest.mark.parametrize("change", ["count", "amount"])
def test_ndd_native_missing_movement_cannot_be_exported_as_complete(change):
    raw = ndd_native(count="4" if change == "count" else "3", last_amount="11,00" if change == "amount" else "10,00")
    try:
        result = pdf_parser.parse_pdf_transactions(raw)
    except PdfFinancialValidationError:
        return
    assert result.parse_metrics["export_recommendation"] == "review_recommended"
    assert any(row.warnings for row in result.canonical_transactions)


def btg_native():
    return grid_pdf([[
        ("BTG Pactual Conta corrente - PJ", .03, .03),
        ("PDF gerado em:", .03, .05), ("04/09/2026 - 09:26", .65, .05),
        ("Período do extrato: 01/10/2026 - 31/10/2026", .03, .07),
        ("Conta: 00123456789-0", .03, .09),
        ("Total de entradas", .4, .10), ("R$ 100,00", .8, .10),
        ("Total de saídas", .4, .13), ("R$ 30,00", .8, .13),
        ("Data lançamento", .03, .2), ("Descrição do lançamento", .2, .2),
        ("Entradas / Saídas (R$)", .65, .2), ("Saldo (R$)", .86, .2),
        ("01/10/2026", .03, .25), ("Saldo de abertura", .2, .25), ("0,00", .86, .25),
        ("02/10/2026", .03, .33), ("PIX RECEBIDO", .2, .32), ("EMPRESA FICTICIA", .2, .345),
        ("100,00", .65, .33), ("100,00", .86, .33),
        ("03/10/2026", .03, .43), ("PIX ENVIADO", .2, .42), ("EMPRESA FICTICIA", .2, .445),
        ("-30,00", .65, .43), ("70,00", .86, .43),
        ("31/10/2026", .03, .51), ("Saldo de fechamento", .2, .51), ("70,00", .86, .51),
    ]])


def test_btg_native_wrapped_descriptions_do_not_disappear(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    result = pdf_parser.parse_pdf_transactions(btg_native())
    assert [row.amount for row in result.transactions] == [100, -30]
    assert all("EMPRESA FICTICIA" in row.description for row in result.transactions)
    assert result.parse_metrics["verified_closing_balance"] == 70
    assert all(row.source_regions for row in result.canonical_transactions)


def test_btg_header_account_survives_native_geometry_when_print_date_is_in_a_separate_column():
    from app.application.default_conversion_pipeline import build_default_conversion_pipeline

    analysis = build_default_conversion_pipeline().run(
        filename="ficticio.pdf", raw_bytes=btg_native(), analysis_id="an_ficticio").analysis_data
    assert analysis.bank_code == "208"
    assert analysis.account_number == "00123456789-0"


def test_banrisul_last_column_system_code_may_be_absent_with_all_controls_present():
    pages = [page.replace("150,00 BRR", "150,00") for page in portal_pages()]
    result = pdf_parser._parse_pdf_transactions_from_page_texts(pages)
    assert [row.amount for row in result.transactions] == [150, -20, -50]
    assert result.parse_metrics["printed_balance_checks"] == 2


def test_bb_separate_sign_may_overlap_the_box_of_a_table_stroke():
    source = bb_source()
    page = source.pages[0]
    lines = [replace(line, text="20,00 I") if line.text == "20,00 D" else line for line in page.lines]
    lines += extraction([("D", .824, .3)]).pages[0].lines
    result = pdf_parser._parse_textract_text_pages(replace(source, pages=[replace(page, lines=lines)]))
    assert [row.amount for row in result.transactions] == [-20]


def test_bb_native_preserves_explicit_signs_on_the_line_below_values(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    raw = grid_pdf([[
        ("Banco do Brasil Cliente Conta atual", .1, .03),
        ("Dt. balancete Dt. movimento Ag. origem", .1, .06),
        ("Histórico", .35, .1), ("Documento", .65, .1), ("Valor R$", .75, .1), ("Saldo", .86, .1),
        ("30/06/2026", .1, .2), ("000 Saldo Anterior", .35, .2), ("100,00", .86, .2), ("D", .91, .21),
        ("01/07/2026", .1, .3), ("144 Pix Enviado", .35, .3), ("20,00", .75, .3), ("D", .8, .31),
        ("100001", .65, .3),
        ("120,00", .86, .3), ("D", .91, .31),
        ("02/07/2026", .1, .4), ("821 Pix Recebido", .35, .4), ("50,00", .75, .4), ("C", .8, .41),
        ("100002", .65, .4),
        ("70,00", .86, .4), ("D", .91, .41),
        ("31/07/2026", .1, .5), ("999 S A L D O", .35, .5), ("70,00", .86, .5), ("D", .91, .51),
    ]])
    result = pdf_parser.parse_pdf_transactions(raw)
    assert [row.amount for row in result.transactions] == [-20, 50]
    assert result.parse_metrics["verified_opening_balance"] == -100
    assert result.parse_metrics["verified_closing_balance"] == -70
    assert result.parse_metrics["balance_consistency_failed"] == 0
    assert not any(row.warnings for row in result.canonical_transactions)


def uniprime_native(amount="30,00 -", balance="70,00"):
    return grid_pdf([[
        ("Uniprime Cooperativa: EXEMPLO", .04, .03), ("Data Atual: 21/01/2020", .6, .03),
        ("Extrato de 01/06/2019 à 30/06/2019", .04, .08),
        ("Saldo Anterior:", .04, .12), ("100,00", .87, .12),
        ("Data", .04, .18), ("Histórico", .25, .18), ("Valor (R$)", .73, .18), ("Saldo (...", .87, .18),
        ("03/06/2...", .04, .25), ("COMPRA FICTICIA", .25, .25), (amount, .73, .25), (balance, .87, .25),
        ("04/06/2...", .04, .32), ("CREDITO FICTICIO", .25, .32), ("50,00 +", .73, .32), ("120,00", .87, .32),
    ]])


def test_uniprime_clipped_year_uses_the_statement_period_not_the_print_date(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    result = pdf_parser.parse_pdf_transactions(uniprime_native())
    assert [(row.date, row.amount) for row in result.transactions] == [("2019-06-03", -30), ("2019-06-04", 50)]
    assert result.parse_metrics["verified_opening_balance"] == 100
    assert result.parse_metrics["verified_closing_balance"] == 120
    assert all("date_year_from_statement_period" in row.warnings for row in result.canonical_transactions)


@pytest.mark.parametrize("balance", ["70,00", "80,00"])
def test_uniprime_clipped_cents_need_two_printed_balances_and_matching_visible_prefix(balance):
    raw = uniprime_native(amount="30,0...", balance=balance)
    if balance != "70,00":
        with pytest.raises(PdfFinancialValidationError):
            pdf_parser.parse_pdf_transactions(raw)
        return
    result = pdf_parser.parse_pdf_transactions(raw)
    assert [row.amount for row in result.transactions] == [-30, 50]
    assert "native_amount_recovered_from_balances" in result.canonical_transactions[0].warnings


def test_banrisul_readable_movements_outside_printed_period_are_preserved_for_review():
    pages = [page.replace("PERIODO: 08/2026", "PERIODO: 01/08/2026 até 02/08/2026") for page in portal_pages()]
    result = pdf_parser.parse_pdf_transactions(generate_native_text_pdf(tuple(tuple(page.splitlines()) for page in pages)))
    assert [row.amount for row in result.transactions] == [150, -20, -50]
    assert "financial_validation_failed" in result.canonical_transactions[0].warnings


def test_banrisul_future_scheduled_section_is_separate_from_completed_movements():
    pages = portal_pages()
    pages[-1] += "\n--- MOVIMENTOS FUTUROS DA CONTA CORRENTE ---\n03/09/2026 1005 PGTO FUTURO 000004 10,00- BDX"
    result = pdf_parser._parse_pdf_transactions_from_page_texts(pages)
    assert [row.amount for row in result.transactions] == [150, -20, -50]
    assert result.parse_metrics["scheduled_entries_excluded"] == 1
    assert "scheduled_transactions_excluded" in result.canonical_transactions[0].warnings


def test_bb_amount_and_balance_in_one_ocr_word_retain_original_evidence_box():
    source = bb_source()
    page = source.pages[0]
    amount = next(line for line in page.lines if line.text == "20,00 D")
    balance = next(line for line in page.lines if line.text == "80,00 C")
    box = {**amount.bbox, "width": balance.bbox["left"] + balance.bbox["width"] - amount.bbox["left"]}
    word = ExtractedWord("merged-cash", 1, 1, "20,00 D 80,00 C", bbox=box, text_type="PRINTED")
    lines = [replace(line, text=word.text, bbox=box, word_ids=(word.id,)) if line.id == amount.id else line
             for line in page.lines if line.id != balance.id]
    result = pdf_parser._parse_textract_text_pages(replace(source, pages=[replace(page, lines=lines, words=[word])]))
    assert [row.amount for row in result.transactions] == [-20]
    assert any(region.left == box["left"] and region.width == box["width"] for region in result.canonical_transactions[0].source_regions)


def infinitepay_native(*, credit="+ 50,00"):
    return grid_pdf([[
        ("InfinitePay infinitepay.io Movimentações", .03, .03),
        ("01 Out, 2026 - 02 Out, 2026", .03, .07),
        ("Saldo inicial", .5, .10), ("+ 100,00", .91, .10),
        ("Total de entradas", .5, .14), ("+ 50,00", .91, .14),
        ("Total de saídas", .5, .18), ("- 30,00", .91, .18),
        ("Saldo final do período", .5, .22), ("+ 120,00", .91, .22),
        ("Data", .03, .27), ("Hora", .12, .27), ("Tipo de transação", .25, .27),
        ("Nome", .45, .27), ("Detalhe", .75, .27), ("Valor (R$)", .91, .27),
        ("01 Out, 2026", .03, .31),
        ("10:00", .12, .35), ("Pix", .25, .35), ("EMPRESA TESTE", .45, .35), ("Enviado", .75, .35), ("- 20,00", .91, .35),
        ("11:00", .12, .40), ("Pix", .25, .40), ("EMPRESA TESTE", .45, .40), ("Recebido", .75, .40), (credit, .91, .40),
        ("Saldo do dia", .25, .45), ("+ 130,00", .91, .45),
        ("02 Out, 2026", .03, .50),
        ("10:00", .12, .55), ("Pix", .25, .55), ("EMPRESA TESTE", .45, .55), ("Enviado", .75, .55), ("- 10,00", .91, .55),
        ("Saldo do dia", .25, .60), ("+ 120,00", .91, .60),
        ("Central de ajuda infinitepay.io", .03, .85),
    ]])


def test_infinitepay_native_uses_coordinates_without_requesting_remote_ocr(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    result = pdf_parser.parse_pdf_transactions(infinitepay_native())
    assert [row.amount for row in result.transactions] == [-20, 50, -10]
    assert result.parse_metrics["verified_opening_balance"] == 100
    assert result.parse_metrics["verified_closing_balance"] == 120
    assert result.parse_metrics["printed_balance_checks"] == 3
    assert all(row.source_regions for row in result.canonical_transactions)


def test_infinitepay_native_does_not_guess_a_missing_amount_sign():
    with pytest.raises(PdfFinancialValidationError):
        pdf_parser.parse_pdf_transactions(infinitepay_native(credit="50,00"))


def caixa_ocr_web(*, ambiguous=False):
    return extraction([
        ("Extrato por período", .2, .02), ("DATA MOV.", .02, .10), ("NR.", .12, .08), ("DOC.", .12, .12),
        ("HISTÓRICO", .2, .10), ("VALOR", .6, .10),
        ("SALDO ANTERIOR", .2, .15), ("0,00" if ambiguous else "100,00 C", .6, .15),
        ("Saldo", .45, .18), ("100,00 C", .6, .18),
        ("01/10/2026 000001 AZCX MC CD", .02, .22), ("10,00 C", .6, .22),
        ("Saldo", .45, .25), ("110,00 C", .6, .25),
        ("01/10/2026", .02, .29), ("000002", .12, .29), ("ENV PIX CH", .2, .29), ("30,00 D", .6, .29),
        ("Saldo", .45, .32), ("80,00 C", .6, .32),
        ("01/10/2026", .02, .36), ("000003", .12, .36), ("TAR PIX", .2, .36), ("2,00 D", .6, .36),
        ("Saldo", .45, .39), ("78,00 C", .6, .39),
        ("01/10/2026 000004 AZCX MC AC", .02, .43), ("5,00 C", .6, .43),
        ("Saldo", .45, .46), ("83,00 C", .6, .46),
        ("01/10/2026 000000 SALDO DIA", .02, .50), ("83,00 C", .6, .50),
        ("Saldo", .45, .53), ("83,00 C", .6, .53),
    ])


@pytest.mark.parametrize("ambiguous", [False, True])
def test_caixa_web_ocr_keeps_first_credit_and_explains_conflicting_openings(ambiguous):
    result = pdf_parser._parse_scanned_pdf_with_textract_text_pages(caixa_ocr_web(ambiguous=ambiguous))
    assert [row.amount for row in result.transactions] == [10, -30, -2, 5]
    assert result.parse_metrics["opening_balance_ambiguous"] == int(ambiguous)
    assert result.parse_metrics["verified_opening_balance"] == (0 if ambiguous else 100)
    assert result.parse_metrics["verified_closing_balance"] == 83
    assert result.parse_metrics["financial_controls_validated"] == int(not ambiguous)
    if ambiguous:
        assert "opening_balance_ambiguous" in result.canonical_transactions[0].warnings


def test_caixa_web_ocr_unpaired_cash_cannot_be_silently_omitted():
    source = caixa_ocr_web()
    page = source.pages[0]
    lines = [line for line in page.lines if not line.text.startswith("01/10/2026 000001")]
    with pytest.raises(PdfFinancialValidationError):
        pdf_parser._parse_scanned_pdf_with_textract_text_pages(replace(source, pages=[replace(page, lines=lines)]))


def test_bb_pending_image_cheque_is_excluded_from_cash_with_an_explicit_warning():
    page = bb_page().replace("31/07/2026 S A L D O", "03/07/2026 99049 123456 Depos CHQ Imagem 1 Dia 357,00 *\n31/07/2026 S A L D O")
    result = pdf_parser._parse_pdf_transactions_from_page_texts([page])
    assert [row.amount for row in result.transactions] == [-194.57, 1100]
    assert result.parse_metrics["non_cash_entries_excluded"] == 1
    assert "pending_deposits_excluded" in result.canonical_transactions[0].warnings


def column_ocr(bank, *, broken=False, orphan=False):
    is_caixa = bank == "caixa"
    head = [("GERENCIADOR CAIXA Extrato por período", .02, .02),
            ("Mês: Abril/2026", .02, .06), ("Período: 1 - 30", .02, .08)] if is_caixa else [
                ("Banco do Nordeste Agência/Conta Corrente: 123/12345-6", .02, .02), ("Detalhamento do Extrato", .02, .04),
                ("Período: 01/04/2026 até 30/04/2026", .02, .06), ("Saldo Anterior: R$ 100,00", .02, .08)]
    cells = head + [("Data Mov." if is_caixa else "Data", .03, .12), ("Histórico", .20, .12),
                    ("Nr. Doc." if is_caixa else "Documento", .45, .12),
                    ("Valor" if is_caixa else "Valor R$", .65, .12), ("Saldo" if is_caixa else "Saldo R$", .85, .12)]
    if is_caixa:
        cells += [("SALDO ANTERIOR", .20, .16), ("0,00", .65, .16), ("100,00 C", .85, .16)]
    cells += [("01/04/2026", .03, .22), ("ENVIO TESTE", .20, .22), ("100001", .45, .22),
              ("20,00 D" if is_caixa else "20,00", .65, .22), ("79,00 C" if broken else "80,00 C" if is_caixa else "80,00", .85, .22),
              ("02/04/2026", .03, .30), ("RECEBIMENTO TESTE", .20, .30), ("100002", .45, .30),
              ("50,00 C" if is_caixa else "50,00", .65, .30), ("130,00 C" if is_caixa else "130,00", .85, .30)]
    if is_caixa:
        cells += [("02/04/2026", .03, .38), ("SALDO DIA", .20, .38), ("000000", .45, .38),
                  ("0,00 C", .65, .38), ("130,00 C", .85, .38)]
    else:
        cells += [("Detalhamento do Saldo", .20, .45), ("Saldo Disponível", .20, .5), ("R$ 999,00", .85, .5)]
    if orphan:
        cells += [("999,00 C" if is_caixa else "999,00", .65, .42)]
    return extraction(cells)


@pytest.mark.parametrize("bank", ["caixa", "nordeste"])
def test_signed_ocr_columns_check_every_printed_balance_and_exclude_control_rows(bank):
    result = pdf_parser._parse_scanned_pdf_with_textract_text_pages(column_ocr(bank))
    assert [row.amount for row in result.transactions] == [-20, 50]
    assert result.parse_metrics["verified_closing_balance"] == 130
    assert result.parse_metrics["balance_consistency_failed"] == 0
    assert all(row.source_regions for row in result.canonical_transactions)
    if bank == "nordeste":
        assert "amount_sign_from_printed_balances" in result.canonical_transactions[0].warnings


@pytest.mark.parametrize("bank", ["caixa", "nordeste"])
@pytest.mark.parametrize("mutation", ["broken", "orphan"])
def test_signed_ocr_columns_cannot_correct_digits_or_drop_unpaired_money(bank, mutation):
    with pytest.raises(PdfFinancialValidationError):
        pdf_parser._parse_scanned_pdf_with_textract_text_pages(column_ocr(bank, **{mutation: True}))


def revolut_native(*, bad_total=False, missing_date=False):
    header = [("Data", .05, .30), ("Descrição", .20, .30), ("Valores descontados Valores recebidos", .55, .30), ("Saldo", .90, .30)]
    return grid_pdf([[
        ("Revolut Sociedade de Crédito Direto S.A. Extrato em BRL", .02, .02),
        ("Resumo do saldo", .02, .06), ("Produto", .05, .10), ("Saldo inicial", .40, .10),
        ("Valores descontados Valores recebidos", .55, .10), ("Saldo final", .90, .10),
        ("Total", .05, .15), ("R$100,00", .40, .15), ("R$31,00" if bad_total else "R$30,00", .55, .15),
        ("R$50,00", .72, .15), ("R$120,00", .88, .15),
        ("Transações da conta de 01 de outubro de 2026 a 02 de outubro de 2026", .02, .22),
    ] + header + [
        ("01 de out. de 2026" if not missing_date else "", .05, .38),
        ("Pagamento teste", .20, .38), ("R$20,00", .55, .38), ("R$80,00", .88, .38),
        ("Destinatário de teste", .20, .40),
        ("01 de out. de 2026", .05, .48), ("Recebimento teste", .20, .48), ("R$50,00", .72, .48), ("R$130,00", .88, .48),
        ("Informe perda ou roubo do seu cartão", .05, .86),
    ], [("Revolut Extrato em BRL", .02, .02)] + header + [
        ("02 de out. de 2026", .05, .38), ("Pagamento teste", .20, .38), ("R$10,00", .55, .38), ("R$120,00", .88, .38),
        ("Informe perda ou roubo do seu cartão", .05, .86),
    ]])


def test_revolut_brl_uses_explicit_column_roles_and_checks_summary_and_each_balance():
    result = pdf_parser.parse_pdf_transactions(revolut_native())
    assert [row.amount for row in result.transactions] == [-20, 50, -10]
    assert result.transactions[0].date == "2026-10-01"
    assert "Destinatário de teste" in result.transactions[0].description
    assert result.parse_metrics["verified_opening_balance"] == 100
    assert result.parse_metrics["verified_closing_balance"] == 120
    assert all(row.source_regions for row in result.canonical_transactions)


@pytest.mark.parametrize("mutation", ["bad_total", "missing_date"])
def test_revolut_brl_cannot_hide_a_summary_mismatch_or_an_unmatched_monetary_row(mutation):
    with pytest.raises(PdfFinancialValidationError):
        pdf_parser.parse_pdf_transactions(revolut_native(**{mutation: True}))


def test_conflicting_opening_reason_survives_pipeline_and_response_contract():
    from app.application.conversion.contracts.payloads import PdfProcessingMetrics
    from app.application.default_conversion_pipeline import build_default_conversion_pipeline

    parsed = pdf_parser._parse_scanned_pdf_with_textract_text_pages(caixa_ocr_web(ambiguous=True))
    analysis = build_default_conversion_pipeline().run(filename="ficticio.pdf", raw_bytes=b"pdf", analysis_id="an_ficticio",
                                                       pdf_parser=lambda *args, **kwargs: parsed).analysis_data
    metrics = PdfProcessingMetrics(**analysis.pdf_processing_metrics)
    assert metrics.opening_balance_ambiguity_reason == "conflicting_printed_balances"
    assert metrics.balance_validation_status == "conflicting_printed_balances"


def test_caixa_saldo_on_next_page_and_missing_decimal_separator_keep_both_source_pages():
    first = caixa_ocr_web().pages[0]
    last = [replace(line, page_number=2, bbox={**line.bbox, "top": line.bbox["top"] - .4})
            for line in first.lines if line.bbox["top"] > .43]
    first = replace(first, lines=[replace(line, text="5 00 C") if line.text == "5,00 C" else line
                                  for line in first.lines if line.bbox["top"] <= .43])
    second = replace(first, page_number=2, lines=last)
    source = replace(caixa_ocr_web(), pages=[first, second])
    result = pdf_parser._parse_scanned_pdf_with_textract_text_pages(source)
    assert [row.amount for row in result.transactions] == [10, -30, -2, 5]
    row = result.canonical_transactions[-1]
    assert {page for page, _ in row.source_line_refs} == {1, 2}
    assert {region.page for region in row.source_regions} == {1, 2}
    assert "ocr_numeric_separator_normalized" in row.warnings


@pytest.mark.parametrize("token,accepted", [("- -20,00", True), ("+ -20,00", False), ("- -21,00", False)])
def test_nordeste_repeated_ocr_sign_needs_matching_signs_digits_and_independent_balances(token, accepted):
    source = column_ocr("nordeste")
    page = source.pages[0]
    source = replace(source, pages=[replace(page, lines=[replace(line, text=token) if line.text == "20,00" else line
                                                        for line in page.lines])])
    if not accepted:
        with pytest.raises(PdfFinancialValidationError):
            pdf_parser._parse_scanned_pdf_with_textract_text_pages(source)
        return
    result = pdf_parser._parse_scanned_pdf_with_textract_text_pages(source)
    assert [row.amount for row in result.transactions] == [-20, 50]
    assert "ocr_duplicate_sign_normalized" in result.canonical_transactions[0].warnings


@pytest.mark.parametrize("token", ["+20,00 D", "-20,00 C"])
def test_caixa_prefix_sign_cannot_contradict_the_cd_indicator_even_when_balances_close(token):
    source = column_ocr("caixa")
    page = source.pages[0]
    source = replace(source, pages=[replace(page, lines=[replace(line, text=token) if line.text == "20,00 D" else line
                                                        for line in page.lines])])
    with pytest.raises(PdfFinancialValidationError):
        pdf_parser._parse_scanned_pdf_with_textract_text_pages(source)
