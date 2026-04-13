import re
import unicodedata
from dataclasses import dataclass

MONTH_PATTERN = r"(?:JAN|FEV|MAR|ABR|MAI|JUN|JUL|AGO|SET|OUT|NOV|DEZ)"
DATE_HEADER_PATTERN = re.compile(rf"\b\d{{2}}\s+{MONTH_PATTERN}\s+\d{{4}}\b")
AMOUNT_PATTERN = re.compile(r"\b-?\d+(?:\.\d{3})*,\d{2}\b")


@dataclass(frozen=True)
class PdfLayoutInference:
    layout_name: str
    confidence: float
    used_fallback: bool


def infer_pdf_layout(text: str) -> PdfLayoutInference:
    normalized = _normalize_text(text)
    nubank_score = _score_nubank_statement(normalized)
    itau_score = _score_itau_statement(normalized)
    generic_score = _score_generic_statement(normalized)

    if nubank_score >= itau_score and nubank_score >= generic_score:
        return PdfLayoutInference(
            layout_name="nubank_statement_ptbr",
            confidence=round(nubank_score, 3),
            used_fallback=False,
        )

    if itau_score >= generic_score:
        return PdfLayoutInference(
            layout_name="itau_statement_ptbr",
            confidence=round(itau_score, 3),
            used_fallback=False,
        )

    return PdfLayoutInference(
        layout_name="generic_statement_ptbr",
        confidence=round(generic_score, 3),
        used_fallback=True,
    )


def _score_nubank_statement(normalized_text: str) -> float:
    score = 0.0
    if "SALDO DO DIA" in normalized_text:
        score += 0.25
    if "TOTAL DE ENTRADAS" in normalized_text:
        score += 0.2
    if "TOTAL DE SAIDAS" in normalized_text:
        score += 0.2
    if "TRANSFERENCIA RECEBIDA PELO PIX" in normalized_text:
        score += 0.2
    if "TRANSFERENCIA ENVIADA PELO PIX" in normalized_text:
        score += 0.15
    return min(score, 1.0)


def _score_itau_statement(normalized_text: str) -> float:
    score = 0.0
    if "EXTRATO CONTA / LANCAMENTOS" in normalized_text:
        score += 0.3
    if "DATA LANCAMENTOS VALOR" in normalized_text:
        score += 0.2
    if "SALDO DO DIA" in normalized_text:
        score += 0.2
    if "LIMITE DA CONTA" in normalized_text:
        score += 0.15
    if "SALDO EM CONTA" in normalized_text:
        score += 0.15
    return min(score, 1.0)


def _score_generic_statement(normalized_text: str) -> float:
    date_count = len(DATE_HEADER_PATTERN.findall(normalized_text))
    amount_count = len(AMOUNT_PATTERN.findall(normalized_text))
    score = min(0.45, date_count * 0.07) + min(0.45, amount_count * 0.02)

    if date_count >= 2 and amount_count >= 2:
        score += 0.1
    if "MOVIMENTACOES" in normalized_text:
        score += 0.1
    return min(score, 1.0)


def _normalize_text(value: str) -> str:
    upper = unicodedata.normalize("NFKD", value.upper())
    without_accents = "".join(ch for ch in upper if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", without_accents).strip()
