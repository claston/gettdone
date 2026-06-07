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


def _contains_token(text: str, token: str) -> bool:
    pattern = rf"(?<![A-Z0-9]){re.escape(token)}(?![A-Z0-9])"
    return re.search(pattern, text) is not None
