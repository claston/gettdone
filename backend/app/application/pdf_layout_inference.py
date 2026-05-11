import re
from dataclasses import dataclass

from app.application.normalization.text import normalize_upper_text

MONTH_PATTERN = r"(?:JAN|FEV|MAR|ABR|MAI|JUN|JUL|AGO|SET|OUT|NOV|DEZ)"
DATE_HEADER_PATTERN = re.compile(rf"\b\d{{2}}\s+{MONTH_PATTERN}\s+\d{{4}}\b")
SLASH_DATE_PATTERN = re.compile(r"\b\d{2}/\d{2}(?:/\d{2,4})?\b")
AMOUNT_PATTERN = re.compile(r"\b[+-]?\d+(?:\.\d{3})*,\d{2}[+-]?\b")
TABLE_HEADER_PATTERN = re.compile(r"\bDATA\b.*\bVALOR\b.*\bSALDO\b")
SPECIFIC_PROFILE_MIN_SCORE = 0.5
SPECIFIC_PROFILE_MIN_MARGIN = 0.05
SPECIFIC_PROFILE_HIGH_CONFIDENCE = 0.7
ANCHOR_MISS_MULTIPLIER = 0.45
BR_PROFILE_TERMS: dict[str, tuple[tuple[str, float], ...]] = {
    "nubank_statement_ptbr": (
        ("NUBANK", 0.7),
        ("CONTA DO NUBANK", 0.16),
        ("TOTAL DE ENTRADAS", 0.22),
        ("TOTAL DE SAIDAS", 0.22),
        ("TRANSFERENCIA RECEBIDA PELO PIX", 0.2),
        ("TRANSFERENCIA ENVIADA PELO PIX", 0.18),
        ("MOVIMENTACOES", 0.1),
        ("SALDO DO DIA", 0.08),
    ),
    "itau_statement_ptbr": (
        ("EXTRATO CONTA / LANCAMENTOS", 0.33),
        ("DATA LANCAMENTOS VALOR", 0.2),
        ("LIMITE DA CONTA", 0.16),
        ("SALDO EM CONTA", 0.15),
        ("SALDO DO DIA", 0.12),
        ("PERIODO DE VISUALIZACAO", 0.08),
    ),
    "santander_statement_ptbr": (
        ("BANCO SANTANDER", 0.3),
        ("EXTRATO DE CONTA CORRENTE", 0.22),
        ("HISTORICO DOCUMENTO VALOR", 0.16),
        ("CONTA CORRENTE", 0.12),
        ("AGENCIA", 0.08),
    ),
    "bradesco_statement_ptbr": (
        ("BANCO BRADESCO", 0.3),
        ("EXTRATO MENSAL", 0.2),
        ("DATA HISTORICO VALOR SALDO", 0.17),
        ("BRADESCO", 0.12),
        ("AGENCIA", 0.08),
    ),
    "bb_statement_ptbr": (
        ("BANCO DO BRASIL", 0.3),
        ("CONTA CORRENTE", 0.16),
        ("EXTRATO", 0.12),
        ("LANCAMENTOS", 0.12),
        ("DOCUMENTO", 0.1),
        ("AGENCIA", 0.08),
    ),
    "caixa_statement_ptbr": (
        ("CAIXA ECONOMICA FEDERAL", 0.32),
        ("EXTRATO DA CONTA CORRENTE", 0.22),
        ("OPERACAO: 001", 0.18),
        ("DATA HISTORICO DOCUMENTO VALOR SALDO", 0.14),
        ("CONTA CORRENTE", 0.1),
        ("AGENCIA", 0.08),
    ),
    "inter_statement_ptbr": (
        ("BANCO INTER", 0.32),
        ("EXTRATO DE CONTA DIGITAL", 0.24),
        ("CONTA DIGITAL", 0.16),
        ("DATA DESCRICAO VALOR SALDO", 0.14),
        ("AGENCIA", 0.08),
    ),
    "sicredi_statement_ptbr": (
        ("SICREDI", 0.34),
        ("EXTRATO CONTA CORRENTE", 0.22),
        ("COOPERATIVA", 0.16),
        ("DATA HISTORICO VALOR SALDO", 0.14),
        ("CONTA CORRENTE", 0.1),
        ("AGENCIA", 0.06),
    ),
}
PROFILE_ANCHORS: dict[str, tuple[str, ...]] = {
    "nubank_statement_ptbr": ("NUBANK", "TOTAL DE ENTRADAS", "TRANSFERENCIA RECEBIDA PELO PIX", "MOVIMENTACOES"),
    "itau_statement_ptbr": ("EXTRATO CONTA / LANCAMENTOS", "LIMITE DA CONTA", "SALDO EM CONTA"),
    "santander_statement_ptbr": ("BANCO SANTANDER", "EXTRATO DE CONTA CORRENTE"),
    "bradesco_statement_ptbr": ("BANCO BRADESCO", "EXTRATO MENSAL"),
    "bb_statement_ptbr": ("BANCO DO BRASIL", "EXTRATO CONTA CORRENTE"),
    "caixa_statement_ptbr": ("CAIXA ECONOMICA FEDERAL", "EXTRATO DA CONTA CORRENTE", "OPERACAO: 001"),
    "inter_statement_ptbr": ("BANCO INTER", "CONTA DIGITAL"),
    "sicredi_statement_ptbr": ("SICREDI", "COOPERATIVA"),
}


@dataclass(frozen=True)
class PdfLayoutInference:
    layout_name: str
    confidence: float
    used_fallback: bool


def infer_pdf_layout(text: str) -> PdfLayoutInference:
    normalized = _normalize_text(text)
    specific_scores = {
        layout_name: _score_layout_profile(layout_name, normalized, terms)
        for layout_name, terms in BR_PROFILE_TERMS.items()
    }
    generic_score = _score_generic_statement(normalized)
    specific_best_name, specific_best_score = max(specific_scores.items(), key=lambda item: item[1])

    if _should_use_specific_profile(specific_best_score=specific_best_score, generic_score=generic_score):
        return PdfLayoutInference(
            layout_name=specific_best_name,
            confidence=round(specific_best_score, 3),
            used_fallback=False,
        )

    return PdfLayoutInference(
        layout_name="generic_statement_ptbr",
        confidence=round(generic_score, 3),
        used_fallback=True,
    )


def _score_layout_profile(layout_name: str, normalized_text: str, terms: tuple[tuple[str, float], ...]) -> float:
    score = 0.0
    for token, weight in terms:
        if token in normalized_text:
            score += weight
    score += _score_statement_structure(normalized_text)

    anchors = PROFILE_ANCHORS.get(layout_name, ())
    if anchors and not any(anchor in normalized_text for anchor in anchors):
        score *= ANCHOR_MISS_MULTIPLIER

    return min(score, 1.0)


def _score_generic_statement(normalized_text: str) -> float:
    date_count = len(DATE_HEADER_PATTERN.findall(normalized_text))
    slash_date_count = len(SLASH_DATE_PATTERN.findall(normalized_text))
    amount_count = len(AMOUNT_PATTERN.findall(normalized_text))
    score = min(0.35, date_count * 0.07) + min(0.3, slash_date_count * 0.04) + min(0.3, amount_count * 0.02)

    if date_count + slash_date_count >= 2 and amount_count >= 2:
        score += 0.1
    if "MOVIMENTACOES" in normalized_text or "LANCAMENTOS" in normalized_text:
        score += 0.1
    if TABLE_HEADER_PATTERN.search(normalized_text):
        score += 0.08
    return min(score, 1.0)


def _score_statement_structure(normalized_text: str) -> float:
    bonus = 0.0
    month_dates = len(DATE_HEADER_PATTERN.findall(normalized_text))
    slash_dates = len(SLASH_DATE_PATTERN.findall(normalized_text))
    amounts = len(AMOUNT_PATTERN.findall(normalized_text))

    if month_dates + slash_dates >= 2 and amounts >= 2:
        bonus += 0.08
    if TABLE_HEADER_PATTERN.search(normalized_text):
        bonus += 0.08
    if "SALDO" in normalized_text and ("VALOR" in normalized_text or "LANCAMENT" in normalized_text):
        bonus += 0.05
    return min(0.2, bonus)


def _should_use_specific_profile(*, specific_best_score: float, generic_score: float) -> bool:
    if specific_best_score >= SPECIFIC_PROFILE_HIGH_CONFIDENCE:
        return True
    if specific_best_score < SPECIFIC_PROFILE_MIN_SCORE:
        return False
    return (specific_best_score - generic_score) >= SPECIFIC_PROFILE_MIN_MARGIN


def _normalize_text(value: str) -> str:
    return normalize_upper_text(value)
