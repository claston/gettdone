"""Positive evidence of an empty statement, never a fallback for extraction failure."""

import re

from app.application.bank_identity import resolve_bank_name
from app.application.normalization.text import normalize_upper_text

_NO_ACTIVITY = re.compile(
    r"^(?:SEM|NAO (?:HOUVE|HA|EXISTE[M]?|FORAM ENCONTRAD[OA]S)|NENHUM[A]?) "
    r"(?:MOVIMENTAC(?:AO|OES)|MOVIMENTOS?|LANCAMENTOS?|TRANSAC(?:AO|OES))"
    r"(?: (?:NO|NESTE|NESSE|DO|PARA O|EM) (?:PERIODO|MES)(?: (?:CONSULTADO|SELECIONADO|INFORMADO|SOLICITADO))?)?[.!]*$"
)
_MONEY = re.compile(r"(?<!\d)\d[\d. ]*[,.]\d{2}(?!\d)")
_DATE = re.compile(r"\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b")
_LEADING_DATE = re.compile(r"^\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?\b")
_BALANCE_LINE = re.compile(r"^(?:\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?\s+)?SALDO\b")


def has_explicit_no_activity(page_texts: list[str] | tuple[str, ...]) -> bool:
    # Require the declaration on every nonblank statement page. A declaration
    # about one section/page must not suppress another page's transactions.
    pages = [text for text in page_texts if text.strip()]
    if not pages:
        return False
    if resolve_bank_name(extracted_text="\n".join(pages)) is None:
        return False
    for page in pages:
        lines = [normalize_upper_text(line).strip() for line in page.splitlines() if line.strip()]
        joined = " ".join(lines)
        if not ("EXTRATO" in joined and re.search(r"\b(?:CONTA|AGENCIA)\b", joined)):
            return False
        if not any(_NO_ACTIVITY.fullmatch(line) for line in lines):
            return False
        for line in lines:
            if re.fullmatch(r"(?:ENTRADAS|SAIDAS):\s*R\$\s*0,00", line):
                continue
            if re.fullmatch(r"[A-Z]+ 20\d{2} \(\d{2}/\d{2}/20\d{2} \d{2}/\d{2}/20\d{2}\)", line):
                continue
            # Balances are context, including nonzero balances on inactive accounts.
            # Other monetary values may be unparsed transactions: keep the usual
            # parser/error path instead of claiming that there was no activity.
            if (_MONEY.search(_DATE.sub("", line)) or _LEADING_DATE.match(line)) and not _BALANCE_LINE.match(line):
                return False
    return True


def has_complete_santander_inactive_summary(page_texts: list[str] | tuple[str, ...]) -> bool:
    """Recognize the complete native PJ template that omits inactive accounts' movements.

    This is intentionally template-specific. Missing rows/sections in an unknown
    layout, OCR, truncated documents or unread pages are not evidence of inactivity.
    """
    pages = [normalize_upper_text(page) for page in page_texts]
    joined = " ".join(pages)
    required = (
        "SANTANDER", "EXTRATO CONSOLIDADO INTELIGENTE", "EXTRATO_PJ_A4_INTELIGENTE",
        "RESUMO -", "PACOTE DE SERVICOS", "PRODUTOS E SERVICOS QUANTIDADE CONTRATADA",
        "VALORES PRATICADOS NO PACOTE DE SERVICOS", "INDICES ECONOMICOS / FINANCEIROS",
        "VOCE E SEU DINHEIRO",
    )
    if not all(marker in joined for marker in required):
        return False
    if re.search(r"\b(?:MOVIMENTAC(?:AO|OES)|LANCAMENTOS|SALDO EM)\b", joined):
        return False
    # Footer completeness is checked against the actual native page count.
    for number, page in enumerate(pages, start=1):
        counters = re.findall(r"\bPAGINA:\s*(\d+)\s*/\s*(\d+)\b", page)
        if counters != [(str(number), str(len(pages)))]:
            return False
        if "EXTRATO_PJ_A4_INTELIGENTE" not in page:
            return False
    if len(re.findall(r"RESUMO - [A-Z]+/20\d{2} .*?AGENCIA \d{3,6} CONTA CORRENTE \d[\d.-]+", joined)) != 1:
        return False
    # All values must belong to known pricing/index rows, never a transaction
    # table. A monetary row that we cannot explain rejects the empty result.
    allowed_value_line = re.compile(
        r"^(?:VALOR DA MENSALIDADE \(R\$\)|\d[\d.]* A \d[\d.]* |ACIMA DE \d|"
        r"IBOVESPA |IGPM |INCC |INPC |IPCA |CDI |TR |POUPANCA |EURO |DOLAR COMERCIAL |SALARIO MINIMO )"
    )
    for page in page_texts:
        for raw_line in page.splitlines():
            line = normalize_upper_text(raw_line)
            if _MONEY.search(_DATE.sub("", line)) and not allowed_value_line.match(line):
                return False
    return True
