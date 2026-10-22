"""Institution and column fingerprints for recovery statement variants."""

import re

from app.application.normalization.text import normalize_upper_text as upper

BB_SISBB = "banco_do_brasil_sisbb_movim_origem_cd_v1"
SICREDI = "sicredi_native_signed_value_balance_v1"
CREDISIS = "credisis_signed_cash_controls_v1"
TRIBANCO = "tribanco_online_conta_vinculada_v1"
MERCADO_PAGO = "mercado_pago_extrato_periodo_al_v1"
CAIXA = "caixa_web_data_efetiva_signed_v1"
INFINITEPAY = "infinitepay_movimentacoes_data_hora_ocr_v1"
PAGBANK = "pagbank_extrato_financeiro_disponivel_ocr_v1"


def identify_sweep_statement(text):
    normalized = upper(text)
    compact = normalized.replace(" ", "")
    if "SISBB - SISTEMA DE INFORMACOES BANCO DO BRASIL" in normalized and all(
        marker in compact for marker in ("DATAMOVIM.", "DEP.ORIGEM", "HISTORICO", "DOCUMENTO", "VALOR", "SALDO")
    ):
        return BB_SISBB
    if "SICREDIFONE" in compact and all(
        marker in compact for marker in ("COOPERATIVA:", "SALDOANTERIOR", "DOCUMENTO", "VALOR(R$)", "SALDO(R$)")
    ):
        return SICREDI
    if "OUVIDORIA CREDISIS" in normalized and all(
        marker in normalized for marker in ("SALDO FINAL", "SALDO ANTERIOR", "CODIGO", "DESCRICAO")
    ):
        return CREDISIS
    if "TRIBANCOONLINE.COM.BR" in compact and "LANCAMENTOS DA CONTA VINCULADA" in normalized:
        return TRIBANCO
    if "MERCADO PAGO INSTITUICAO DE PAGAMENTO" in normalized and all(
        marker in normalized for marker in ("EXTRATO DE CONTA", "ID DA OPERACAO", "SALDO INICIAL", "SALDO FINAL", " AL ")
    ):
        return MERCADO_PAGO
    if "SAC CAIXA" in normalized and all(
        marker in normalized
        for marker in (
            "SALDO ANTERIOR AO PERIODO SOLICITADO",
            "DATA EFETIVA",
            "DOCUMENTO HISTORICO VALOR SALDO",
        )
    ):
        return CAIXA
    if re.search(r"(?<![A-Z0-9.-])(?:WWW\.)?INFINITEPAY\.IO(?![A-Z0-9.-])", normalized) and all(
        marker in normalized for marker in ("TIPO DE TRANSACAO", "DETALHE", "VALOR (R$)")
    ):
        return INFINITEPAY
    if "PAGBANK" in normalized and "EXTRATO FINANCEIRO" in normalized and "PAGSEGURO" in normalized:
        return PAGBANK
    if all(marker in normalized for marker in ("SALDO DO DIA:", "FALE COM A GENTE", "0800 940 9999", "0800 940 7772")):
        return "banco_inter_extrato_conta_corrente_saldo_transacao_v1"
    return None
