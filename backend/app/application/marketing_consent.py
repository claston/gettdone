from __future__ import annotations

LEGACY_PRODUCT_UPDATES_CONSENT_VERSION = 1
CURRENT_PRODUCT_UPDATES_CONSENT_VERSION = 2

PRODUCT_UPDATES_CONSENT_TEXT_BY_VERSION = {
    LEGACY_PRODUCT_UPDATES_CONSENT_VERSION: (
        "Quero receber novidades e atualizações do OFX Simples por e-mail. "
        "Posso cancelar quando quiser."
    ),
    CURRENT_PRODUCT_UPDATES_CONSENT_VERSION: (
        "Aceito receber por e-mail novidades, conteúdos, ofertas e comunicações de marketing do "
        "OFX Simples, inclusive sobre produtos e serviços de parceiros. Posso cancelar quando quiser."
    ),
}


def normalize_product_updates_consent_version(
    *,
    opted_in: bool,
    consent_version: int | None,
) -> int | None:
    if not opted_in:
        return None
    if consent_version is None:
        return LEGACY_PRODUCT_UPDATES_CONSENT_VERSION
    return max(LEGACY_PRODUCT_UPDATES_CONSENT_VERSION, int(consent_version))
