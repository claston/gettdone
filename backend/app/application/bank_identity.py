from __future__ import annotations

import re

from app.application.bank_catalog import load_bank_catalog
from app.application.layout_profiles.registry import get_layout_profile
from app.application.normalization.text import normalize_upper_text

_GENERIC_LAYOUT_NAMES = {"generic_statement_ptbr"}
_LEGACY_LAYOUT_BANK_NAMES = {
    "bb_statement_ptbr": "Banco do Brasil",
    "bradesco_statement_ptbr": "Bradesco",
    "caixa_statement_ptbr": "Caixa Economica Federal",
    "inter_statement_ptbr": "Banco Inter",
    "itau_statement_ptbr": "Itau",
    "nubank_statement_ptbr": "Nubank",
    "santander_statement_ptbr": "Santander",
    "sicredi_statement_ptbr": "Sicredi",
}
_TEXT_BANK_HINTS = (
    ("BANCO DO BRASIL", "Banco do Brasil"),
    ("CAIXA ECONOMICA FEDERAL", "Caixa Economica Federal"),
    ("BANCO SANTANDER", "Santander"),
    ("SANTANDER", "Santander"),
    ("BRADESCO", "Bradesco"),
    ("ITAU EMPRESAS", "Itau"),
    ("ITAU", "Itau"),
    ("NUBANK", "Nubank"),
    ("BANCO INTER", "Banco Inter"),
    ("INTER", "Banco Inter"),
    ("SICREDI", "Sicredi"),
)
_BRADESCO_TRANSACTION_ANCHOR = "RENTAB.INVEST FACILCRED"
_BRADESCO_TRANSACTION_SUPPORTING_HINTS = (
    "TED-TRANSF ELET DISPON",
    "PIX QR CODE DINAMIC",
    "PAGTO ELETRON COBRANCA",
    "TARIFA BANCARIA LIQUIDACAO QRCODE PIX",
    "CIELO VDA DEBITO MASTER",
)
_UNLISTED_INSTITUTION_MARKERS = (
    "BANCO",
    "COOPERATIVA DE CREDITO",
    "COOPERATIVA DE CRÉDITO",
    "BANCO COOPERATIVO",
    "INSTITUICAO DE PAGAMENTO",
    "INSTITUIÇÃO DE PAGAMENTO",
    "SOCIEDADE DE CREDITO",
    "SOCIEDADE DE CRÉDITO",
    "FINANCEIRA",
)
_PRIVATE_HEADER_MARKERS = (
    "CLIENTE",
    "TITULAR",
    "NOME",
    "CPF",
    "CNPJ",
    "AGENCIA",
    "AGÊNCIA",
    "CONTA",
    "DOCUMENTO",
)
_GENERIC_INSTITUTION_WORDS = frozenset(
    {
        "A",
        "BANCO",
        "COOPERATIVA",
        "COOPERATIVO",
        "CREDITO",
        "DA",
        "DAS",
        "DE",
        "DO",
        "DOS",
        "FINANCEIRA",
        "INSTITUICAO",
        "LTDA",
        "PAGAMENTO",
        "S",
        "SA",
        "SOCIEDADE",
    }
)


def resolve_bank_name(
    *,
    layout_inference_name: str | None = None,
    extracted_text: str | None = None,
) -> str | None:
    layout_name = str(layout_inference_name or "").strip()
    if layout_name:
        profile = get_layout_profile(layout_name)
        if profile is not None and profile.bank:
            return profile.bank
        legacy_bank = _LEGACY_LAYOUT_BANK_NAMES.get(layout_name.lower())
        if legacy_bank:
            return legacy_bank

    return _match_bank_name_in_text(extracted_text)


def resolve_conversion_model_label(
    *,
    layout_inference_name: str | None = None,
    bank_name: str | None = None,
) -> str:
    layout_name = str(layout_inference_name or "").strip()
    normalized_layout = layout_name.lower()
    if layout_name and normalized_layout not in _GENERIC_LAYOUT_NAMES:
        return layout_name

    clean_bank_name = str(bank_name or "").strip()
    if clean_bank_name:
        return f"Nao identificado - {clean_bank_name}"
    return "Nao identificado"


def _match_bank_name_in_text(extracted_text: str | None) -> str | None:
    normalized_text = normalize_upper_text(extracted_text or "")
    if not normalized_text:
        return None

    header_candidate = _match_unlisted_institution_header(extracted_text)
    if header_candidate is not None:
        return _match_known_bank_name(header_candidate) or header_candidate
    transaction_fingerprint = _match_bank_transaction_fingerprint(normalized_text)
    if transaction_fingerprint is not None:
        return transaction_fingerprint
    return _match_known_bank_name(normalized_text)


def _match_bank_transaction_fingerprint(normalized_text: str) -> str | None:
    if _BRADESCO_TRANSACTION_ANCHOR not in normalized_text:
        return None
    supporting_hits = sum(hint in normalized_text for hint in _BRADESCO_TRANSACTION_SUPPORTING_HINTS)
    return "Bradesco" if supporting_hits >= 2 else None


def _match_known_bank_name(normalized_text: str) -> str | None:

    best_name = ""
    best_score = -1
    for token, bank_name in _TEXT_BANK_HINTS:
        if _contains_token(normalized_text, token):
            score = len(token) + 100
            if score > best_score:
                best_name = bank_name
                best_score = score

    for record in load_bank_catalog():
        display_name = record.short_name or record.name
        for candidate in (display_name, record.name, *record.aliases):
            normalized_candidate = normalize_upper_text(candidate)
            if len(normalized_candidate) < 4:
                continue
            if not _contains_token(normalized_text, normalized_candidate):
                continue
            score = len(normalized_candidate)
            if candidate == display_name:
                score += 20
            elif candidate == record.name:
                score += 10
            if score > best_score:
                best_name = display_name
                best_score = score

    return best_name or None


def _match_unlisted_institution_header(extracted_text: str | None) -> str | None:
    lines = [normalize_upper_text(line) for line in str(extracted_text or "").splitlines() if line.strip()]
    for line in lines[:12]:
        if _is_transaction_table_header(line):
            break
        candidate = _institution_candidate_from_line(line)
        if candidate is not None:
            return candidate
    return None


def _institution_candidate_from_line(line: str) -> str | None:
    candidate = line
    for marker in _PRIVATE_HEADER_MARKERS:
        candidate = re.split(rf"\b{re.escape(normalize_upper_text(marker))}\b", candidate, maxsplit=1)[0]
    candidate = re.sub(r"^EXTRATO(?: BANCARIO)?(?: DA| DE| DO)?[\s:|-]*", "", candidate).strip(" -:|")
    candidate = re.sub(r"[^A-Z0-9 .,&()'/-]+", " ", candidate)
    candidate = re.sub(r"\s+", " ", candidate).strip(" .,:;|-/")
    if not candidate or len(candidate) > 120 or re.search(r"\d{4,}", candidate):
        return None
    if not any(
        re.search(rf"(?<![A-Z0-9]){re.escape(normalize_upper_text(marker))}(?![A-Z0-9])", candidate)
        for marker in _UNLISTED_INSTITUTION_MARKERS
    ):
        return None
    distinctive_words = {
        word
        for word in re.findall(r"[A-Z0-9]+", candidate)
        if word not in _GENERIC_INSTITUTION_WORDS and len(word) >= 2
    }
    return candidate if distinctive_words else None


def _is_transaction_table_header(line: str) -> bool:
    return "DATA" in line and any(marker in line for marker in ("HISTORICO", "DESCRICAO", "VALOR", "LANCAMENTO"))


def _contains_token(text: str, token: str) -> bool:
    pattern = rf"(?<![A-Z0-9]){re.escape(token)}(?![A-Z0-9])"
    return re.search(pattern, text) is not None
