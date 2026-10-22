"""Structural fingerprints for exports whose logos may not be text objects."""

import re

from app.application.normalization.text import normalize_upper_text

SUMUP_PERIOD = "sumup_extrato_periodo_data_hora_v1"
SANTANDER_GROUPED = "santander_internet_banking_empresarial_periodo_agrupado_v1"
CRESOL_INACTIVE = "cresol_posicao_consolidada_sem_movimento_v1"
CONTA_AZUL = "conta_azul_relatorio_extrato_valor_saldo_v1"
BRADESCO_MONTHLY_OCR = "bradesco_extrato_mensal_dia_historico_valor_ocr_v1"


def identify_structured_statement(text: str) -> str | None:
    normalized = normalize_upper_text(text)
    if all(marker in normalized for marker in (
        "EXTRATO BANCARIO POR PERIODO", "DATA E HORA", "TIPO", "DESCRICAO", "EXTRATO BANCARIO DE",
    )) and "4003-6338" in normalized and "0800 023 6510" in normalized:
        return SUMUP_PERIOD
    if ("INTERNET BANKING EMPRESARIAL" in normalized and "SANTANDER" in normalized
            and re.search(r"\b\d{1,2} DE [A-Z]+ DE 20\d{2}\b", normalized)
            and re.search(r"\b(?:DEBITO|CREDITO)\s+R\$", normalized)
            and not any(marker in normalized for marker in (
                "BUSQUE POR UM PERIODO", "EXIBINDO RESULTADOS PARA", "VER EXTRATO 365 DIAS", "SALDO DO DIA",
            ))):
        return SANTANDER_GROUPED
    if all(marker in normalized for marker in (
        "SALDO EM CONTA LIMITE DE CREDITO SALDO DISPONIVEL", "CONSULTA POSICAO CONSOLIDADA EM",
        "PERIODO DE", "SALDO ANTERIOR:", "NENHUM LANCAMENTO ENCONTRADO",
    )):
        return CRESOL_INACTIVE
    if all(marker in normalized for marker in (
        "RELATORIO DE EXTRATO", "RECEITAS REALIZADAS", "DESPESAS REALIZADAS", "CLIENTE/FORNECEDOR",
        "CATEGORIA", "SITUACAO", "VALOR (R$)", "SALDO (R$)",
    )):
        return CONTA_AZUL
    return None


def is_bradesco_monthly_ocr(text: str) -> bool:
    normalized = normalize_upper_text(text)
    return all(marker in normalized for marker in (
        "BRADESCO", "EXTRATO MENSAL", "REDE BRADESCO", "CONSULTA DE SALDO", "DIA", "HISTORICO", "N.DOCTO", "VALOR",
    ))
