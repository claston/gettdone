"""Synthetic identity headers reproducing shapes found in the native-only audit."""

import pytest

from app.application.statement_identity import identify_statement


@pytest.mark.parametrize("timestamp", ["30/05/2026, 15:12 Internet Banking", "01/09/2026 13:53:41"])
def test_generation_timestamp_before_identity_does_not_end_header(timestamp):
    source = identify_statement({"bank_code": "748"}, page_texts=[
        f"{timestamp}\nAssociado: EMPRESA AURORA LTDA\n"
        "Cooperativa: 0007 Conta Corrente: 00123-4 Impresso em 30/05/2026\n"
        "Data Descrição Valor Saldo\n01/05/2026 PIX 10,00 110,00"
    ])
    assert source["holder_name"] == "EMPRESA AURORA LTDA"
    assert source["cooperative"] == "0007"
    assert source["account_number"] == "00123-4"
    assert source["confidence"] == "source_account"


@pytest.mark.parametrize("next_field", ["Agência/Conta Corrente:", "Agência:", "CNPJ:", "Mês:"])
def test_empty_holder_does_not_consume_another_field_label(next_field):
    source = identify_statement({}, page_texts=[f"Extrato de Conta Corrente\nTitular:\n{next_field}\nData Histórico Valor"])
    assert source["holder_name"] == ""
    assert source["holder_tax_id"] == ""


def test_account_and_tax_header_can_precede_unlabelled_holder_name():
    source = identify_statement({"bank_code": "341"}, page_texts=[
        "Agência   0001 Conta 0098207-6 CNPJ 12.345.678/0001-90EMPRESA AURORA\n"
        "Saldo total Limite da conta Utilizado Disponível\n"
        "Data Lançamentos Razão Social CNPJ/CPF Valor (R$) Saldo (R$)\n"
        "01/05/2026 PIX ENVIADO TERCEIRO 98.765.432/0001-10 -10,00"
    ])
    assert source["holder_name"] == "EMPRESA AURORA"
    assert source["holder_tax_id"] == "12345678000190"
    assert source["branch"] == "0001"
    assert source["account_number"] == "0098207-6"
    assert source["confidence"] == "strong"


def test_statement_title_and_holder_before_tax_and_displaced_account_labels():
    source = identify_statement({"bank_code": "323", "bank_branch": "1", "account_number": "00123456789"}, page_texts=[
        "Data de geração: 01-06-2026\nBanco Instituição de Pagamento Ltda. CNPJ 98.765.432/0001-10\n"
        "1/1\nEXTRATO DE CONTA\nEMPRESA AURORA LTDA\n"
        "CPF/CNPJ: 12345678000190  1  00123456789Agência: Conta:\n"
        "De 01-05-2026 al 31-05-2026Periodo:\nDETALHE DOS MOVIMENTOS\n"
        "Data Descrição ID da operação Valor Saldo\n01-05-2026 PIX 10,00 110,00"
    ])
    assert source["holder_name"] == "EMPRESA AURORA LTDA"
    assert source["holder_tax_id"] == "12345678000190"
    assert source["account_number"] == "00123456789"
    assert source["confidence"] == "strong"


def test_current_account_number_glued_to_holder_in_customer_header():
    source = identify_statement({"bank_code": "001"}, page_texts=[
        "Consultas - Extrato de conta corrente\n01/09/2026 13:53:41\nCliente - Conta atual\n"
        "Agência 0777-3\nConta corrente 00123-4EMPRESA AURORA LTDA\nPeríodo do extrato 08 / 2026\n"
        "Lançamentos\nDt. balancete Dt. movimento Ag. origem Lote Histórico Documento Valor Saldo\n"
        "01/08/2026 0777 PIX 10,00\nConta corrente 99999-0TERCEIRO LTDA"
    ])
    assert source["holder_name"] == "EMPRESA AURORA LTDA"
    assert source["branch"] == "0777-3"
    assert source["account_number"] == "00123-4"
    assert source["holder_tax_id"] == ""
    assert source["confidence"] == "source_account"


def test_unlabelled_name_next_to_origin_account_is_explicit_header_evidence():
    source = identify_statement({"bank_code": "033"}, page_texts=[
        "Aplicativo Banco Empresas\nEMPRESA AURORA LTDA Agência: 0001 Conta: 0012345\n"
        "Períodos: 01/05/2026 a 31/05/2026 Data/Hora: 01/06/2026 as 15h32\n"
        "Data Histórico Documento Valor (R$) Saldo (R$)\n01/05/2026 Pix Recebido 11122233344 10,00 110,00"
    ])
    assert source["holder_name"] == "EMPRESA AURORA LTDA"
    assert source["branch"] == "0001"
    assert source["account_number"] == "0012345"
    assert source["holder_tax_id"] == ""


def test_generic_bank_or_counterparty_tax_is_never_assumed_to_be_holder():
    source = identify_statement({"bank_code": "341"}, page_texts=[
        "BANCO EXEMPLO S.A. CNPJ 98.765.432/0001-10\nEXTRATO DE CONTA\n"
        "Agência 0001 Conta 00123-4\nData Histórico Valor\n"
        "01/05/2026 PIX 10,00\nTitular: TERCEIRO LTDA CNPJ 12.345.678/0001-90"
    ])
    assert source["holder_name"] == source["holder_tax_id"] == ""


@pytest.mark.parametrize("header", [
    "29/10/2026 EXTRATO CONTA CORRENTE 13:52:36\nCOOP.: 0007-1 / COOPERATIVA EXEMPLO\nCONTA: 00123-4 / EMPRESA AURORA LTDA",
    "EMPRESA AURORA LTDA\nAgência 0007-1 Conta 00123-4",
    "Agência........: 0007-1 - CENTRO CONTA...: 00123-4\nNOME...........: EMPRESA AURORA LTDA",
])
def test_shared_origin_header_shapes(header):
    source = identify_statement({"bank_code": "756"}, page_texts=[header + "\nData Histórico Valor\n01/05/2026 PIX 10,00"])
    assert source["holder_name"] == "EMPRESA AURORA LTDA"
    assert source["account_number"] == "00123-4"
    assert source["branch"] == "0007-1" or source["cooperative"] == "0007-1"
    assert source["confidence"] == "source_account"


def test_account_section_stacked_labels_and_native_font_hyphen():
    source = identify_statement({"bank_code": "197"}, page_texts=[
        "Extrato de conta corrente\nDados da conta\nNome\nEMPRESA AURORA LTDA\n"
        "Documento\n12.345.678/0001\ue08890\nInstituição\nBANCO EXEMPLO S.A.\n"
        "Agência\n0001\nConta\n001234\ue0885\nPeríodo: de 01/05/2026 a 31/05/2026\n"
        "DATA TIPO DESCRIÇÃO VALOR SALDO CONTRAPARTE\n01/05/26 Entrada 10,00 110,00"
    ])
    assert source["holder_name"] == "EMPRESA AURORA LTDA"
    assert source["holder_tax_id"] == "12345678000190"
    assert source["branch"] == "0001" and source["account_number"] == "001234-5"


def test_tax_and_name_adjacent_to_origin_account():
    source = identify_statement({"bank_code": "104"}, page_texts=[
        "EMPRESA AURORA LTDA\nCNPJ: 12.345.678/0001-90\nAgência: 0001 Conta: 00123-4\n"
        "Data Histórico Valor\n01/05/2026 PIX 10,00\nCNPJ: 98.765.432/0001-10"
    ])
    assert source["holder_name"] == "EMPRESA AURORA LTDA"
    assert source["holder_tax_id"] == "12345678000190"


def test_unlabelled_list_of_payees_never_becomes_an_owner():
    source = identify_statement({}, page_texts=[
        "Nome CPF ou CNPJ Forma de pagamento Data de Pagamento\n"
        "CLIENTE SINTETICO 12345678000190 Boleto Bancário 01/05/2026\n"
        "Titular: OUTRA CONTRAPARTE\nConta: 99999-0"
    ])
    assert source["holder_name"] == source["holder_tax_id"] == source["account_number"] == ""


def test_different_tax_ids_in_repeated_owner_headers_stay_ambiguous():
    header = "EMPRESA AURORA LTDA\nCNPJ: {}\nAgência: 0001 Conta: 00123-4\nData Histórico Valor"
    source = identify_statement({"bank_code": "104"}, page_texts=[
        header.format("12.345.678/0001-90"), header.format("98.765.432/0001-10")
    ])
    assert source["holder_tax_id"] == "" and source["confidence"] == "ambiguous"


def test_displaced_period_dates_never_override_a_known_source_account():
    source = identify_statement({"bank_code": "237", "bank_branch": "0007", "account_number": "00123-4"}, page_texts=[
        "Extrato de: Ag: | CC: 01/05/2026 31/05/20260007 00123-4 | Entre e\n"
        "Agência | Conta Total Disponível (R$) Total (R$)\n0007 | 00123-4 100,00 100,00\n"
        "Data Lançamento Dcto. Crédito (R$) Débito (R$) Saldo (R$)"
    ])
    assert source["branch"] == "0007" and source["account_number"] == "00123-4"
    assert source["holder_name"] == ""


def test_explicit_owner_wins_over_spaced_bank_logo_and_movement_names_are_excluded():
    source = identify_statement({"bank_code": "041"}, page_texts=[
        "B A N R I S U L\nAGENCIA: 0007\nCONTA..: 0012345\nNOME...: EMPRESA AURORA LTDA\n"
        "DIA HISTORICO DOCUMENTO V A L O R\n01 PIX 123 10,00\nNOME: CONTRAPARTE LTDA"
    ])
    assert source["holder_name"] == "EMPRESA AURORA LTDA" and source["confidence"] == "source_account"


def test_client_label_with_branch_product_account_composite():
    source = identify_statement({"bank_code": "104"}, page_texts=[
        "Extrato por período\nCliente:EMPRESA AURORA LTDA\nConta: 0007 | 1292 | 00123-4\nData Mov. Histórico Valor Saldo"
    ])
    assert source["holder_name"] == "EMPRESA AURORA LTDA"
    assert source["branch"] == "0007" and source["account_number"] == "00123-4"


def test_title_on_same_line_as_date_does_not_create_a_spurious_account():
    source = identify_statement({"bank_code": "756"}, page_texts=[
        "EXTRATO DE CONTA CORRENTE 01/06/2026 - 11:00:38\n"
        "Cooperativa: 0007 / COOPERATIVAConta: 00123-4 / EMPRESA AURORA LTDAPeriodo: 01/05/2026 - 31/05/2026\n"
        "HISTÓRICO DE MOVIMENTAÇÃO"
    ])
    assert source["account_number"] == "00123-4" and len(source["accounts"]) == 1


def test_monthly_title_is_not_the_customer_name():
    source = identify_statement({"bank_code": "341"}, page_texts=["extrato\nmensal\nag 0007 cc 00123-4\nData Histórico Valor"])
    assert source["holder_name"] == "" and source["account_number"] == "00123-4"


def test_native_layout_recovers_displaced_owner_table_without_ocr(monkeypatch):
    from io import BytesIO

    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import DecodedStreamObject, NameObject

    from app.application.statement_identity import identify_native_statement
    from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf

    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    reader = PdfReader(BytesIO(generate_native_text_pdf(((),))))
    writer = PdfWriter()
    page = writer.add_page(reader.pages[0])
    # The content stream lists values before their labels; only native coordinates restore the row.
    commands = [
        "BT /F1 10 Tf 1 0 0 1 48 780 Tm (Agencia: 0001) Tj ET",
        "BT /F1 10 Tf 1 0 0 1 48 760 Tm (Conta: 00123-4) Tj ET",
        "BT /F1 10 Tf 1 0 0 1 48 720 Tm (EMPRESA AURORA LTDA) Tj ET",
        "BT /F1 10 Tf 1 0 0 1 48 700 Tm (Atividade) Tj ET",
        "BT /F1 10 Tf 1 0 0 1 400 720 Tm (12.345.678/0001-90) Tj ET",
        "BT /F1 10 Tf 1 0 0 1 48 740 Tm (Nome/Razao Social) Tj ET",
        "BT /F1 10 Tf 1 0 0 1 400 740 Tm (CPF/CNPJ) Tj ET",
    ]
    stream = DecodedStreamObject()
    stream.set_data("\n".join(commands).encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    raw = output.getvalue()
    pages = [p.extract_text() for p in PdfReader(BytesIO(raw)).pages]
    assert identify_statement({"bank_code": "133"}, page_texts=pages)["holder_name"] == ""
    source = identify_native_statement({"bank_code": "133"}, raw_bytes=raw, page_texts=pages)
    assert source["holder_name"] == "EMPRESA AURORA LTDA"
    assert source["holder_tax_id"] == "12345678000190"
    assert source["account_number"] == "00123-4"
    assert source["references"] and all(ref["page"] == 1 and ref["text_mode"] == "layout" for ref in source["references"])


def test_conflicting_native_representations_require_review_even_without_more_fields():
    from app.application.statement_identity import _prefer_native_source

    source = identify_statement({"bank_code": "104"}, page_texts=[
        "Titular: EMPRESA AURORA LTDA\nAgência: 0001 Conta: 00123-4"
    ])
    candidate = identify_statement({"bank_code": "104"}, page_texts=[
        "Titular: OUTRO CLIENTE LTDA\nAgência: 0001 Conta: 00123-4"
    ])
    result = _prefer_native_source(source, candidate, text_mode="layout")
    assert result["holder_name"] == "" and result["confidence"] == "ambiguous"


def test_native_layout_failure_keeps_existing_identity_without_calling_other_extractors():
    from app.application.statement_identity import identify_native_statement

    source = identify_native_statement({"bank_code": "104"}, raw_bytes=b"not a PDF", page_texts=[
        "Titular: EMPRESA AURORA LTDA\nAgência: 0001 Conta: 00123-4"
    ])
    assert source["holder_name"] == "EMPRESA AURORA LTDA" and source["confidence"] == "source_account"


def test_month_and_page_number_after_account_are_not_a_holder_name():
    source = identify_statement({"bank_code": "341"}, page_texts=[
        "extrato\nmensal\nag 0007 cc 00123-4 jul 2026 001|004\nData Histórico Valor",
        "extrato\nmensal\nag 0007 cc 00123-4 jul 2026 002|004\nData Histórico Valor",
    ])
    assert source["holder_name"] == "" and source["confidence"] == "missing"
    assert source["account_number"] == "00123-4"


def test_spaced_agency_check_digit_is_preserved_in_owner_table():
    source = identify_statement({"bank_code": "133"}, page_texts=[
        "Agência: 0007 - 6 - CENTRO\nConta: 00123-4\n"
        "Nome/Razão Social CPF/CNPJ\nEMPRESA AURORA LTDA 12.345.678/0001-90\nData Movimento Lançamento Valor"
    ])
    assert source["branch"] == "0007-6" and source["holder_tax_id"] == "12345678000190"


def test_account_section_pronoun_is_not_a_holder_name():
    source = identify_statement({"bank_code": "341"}, page_texts=[
        "extrato mensal\nag 0007 cc 00123-4\nMinha conta 00123-4 Minha agência 0007\nData Histórico Valor"
    ])
    assert source["holder_name"] == "" and source["confidence"] == "missing"


def test_metadata_without_header_evidence_does_not_erase_a_printed_account():
    from app.application.statement_identity import _prefer_native_source

    source = identify_statement({"bank_code": "104"}, page_texts=[
        "Cliente: EMPRESA AURORA LTDA\nConta: 0007 | 1292 | 00123-4\nData Histórico Valor"
    ])
    candidate = identify_statement({"bank_code": "104", "account_number": "0007"}, page_texts=[])
    result = _prefer_native_source(source, candidate, text_mode="layout")
    assert result["account_number"] == "00123-4" and result["confidence"] == "source_account"


def test_period_prefixed_holder_is_separate_from_issuer_copyright_and_tax():
    source = identify_statement({"bank_code": "208"}, page_texts=[
        "Data Descrição Valor\n01/05/2026 PIX 10,00\nExtrato conta corrente\n"
        "Período de 01/05/2026 a 31/05/2026 – EMPRESA AURORA LTDA\n"
        "CNPJ 12.345.678/0001-90 · Banco 208 · Agência 50 · Conta 0012345 · PDF gerado em 01/06/2026\n"
        "Fale com nossa central de atendimento\nOuvidoria: 0800 000 0000\n"
        "© 2026 BANCO EXEMPLO. CNPJ 98.765.432/0001-10. 1 / 1"
    ])
    assert source["holder_name"] == "EMPRESA AURORA LTDA"
    assert source["holder_tax_id"] == "12345678000190"
    assert source["account_number"] == "0012345" and source["confidence"] == "strong"
