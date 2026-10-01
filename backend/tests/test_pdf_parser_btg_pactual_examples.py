from __future__ import annotations

from app.application import pdf_parser as pdf_parser_module
from app.application.ofx_writer import build_ofx_statement

BTG_PJ_STATEMENT_TEXT = """
BTG Pactual
CONTA CORRENTE - PJ PDF GERADO EM: 04/09/2026 - 09:26:11
RAZÃO SOCIAL CNPJ BANCO AGÊNCIA CONTA
EMPRESA EXEMPLO 00.000.000/0001-00 208 50 012345678
PERÍODO DO EXTRATO: 01/07/2026 - 31/07/2026
01. CONTA CORRENTE
SALDO DE ABERTURA EM 01/07/2026: R$ 0,91
SALDO DE FECHAMENTO EM 31/07/2026: R$ 146.404,22
SALDO BLOQUEADO EM 04/09/2026: R$ 0,00
TOTAL DE ENTRADAS R$ 685.022,61
TOTAL DE SAÍDAS R$ 538.619,30
02. LANÇAMENTOS
DATA LANÇAMENTO DESCRIÇÃO DO LANÇAMENTO ENTRADAS / SAÍDAS (R$) SALDO (R$)
01/07/2026 SALDO DE ABERTURA 0,91
10/07/2026 APLICAÇÃO CONTA REMUNERADA 254.733,94 254.734,85
10/07/2026 DÉBITO NA CONTA CORRENTE -254.733,94 0,91
10/07/2026 PIX ENVIADO PARA CLIENTE EXEMPLO -1.000,00 -999,09
10/07/2026 TED RECEBIDA DE EMPRESA EXEMPLO 321.914,00 254.734,85
31/07/2026 VALOR DE RENDIMENTO REMUNERA+ 2,03 146.404,22
31/07/2026 SALDO DE FECHAMENTO 146.404,22
FALE COM NOSSA CENTRAL DE ATENDIMENTO
3003-6299 0800-777-6299 SAC: 0800-772-2827
OUVIDORIA: 0800-722-0048
"""


def test_parse_pdf_transactions_supports_btg_pj_current_account(monkeypatch) -> None:
    monkeypatch.setattr(
        pdf_parser_module,
        "_read_native_pdf_page_texts",
        lambda raw_bytes: [BTG_PJ_STATEMENT_TEXT],
    )
    monkeypatch.setattr(
        pdf_parser_module,
        "_read_layout_native_pdf_page_texts",
        lambda raw_bytes: [BTG_PJ_STATEMENT_TEXT],
    )

    result = pdf_parser_module.parse_pdf_transactions(b"%PDF synthetic")

    assert result.layout.layout_name == "btg_pactual_conta_corrente_pj_v1"
    assert result.layout.confidence >= 0.95
    assert result.parse_metrics["selected_parser"] == "layout_specific_btg_pactual"
    assert [transaction.amount for transaction in result.transactions] == [
        254733.94,
        -254733.94,
        -1000.00,
        321914.00,
        2.03,
    ]
    assert [transaction.running_balance for transaction in result.canonical_transactions] == [
        254734.85,
        0.91,
        -999.09,
        254734.85,
        146404.22,
    ]
    assert all("SALDO DE" not in transaction.description for transaction in result.transactions)

    ofx = build_ofx_statement(result.transactions)
    assert ofx.count("<STMTTRN>") == 5
    assert "<TRNAMT>321914.00" in ofx
