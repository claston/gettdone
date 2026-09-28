from __future__ import annotations

from pathlib import Path

from app.application.marketing_consent import (
    CURRENT_PRODUCT_UPDATES_CONSENT_VERSION,
    PRODUCT_UPDATES_CONSENT_TEXT_BY_VERSION,
)

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"


def test_signup_exposes_broad_marketing_consent_without_version_details() -> None:
    source = (FRONTEND_DIR / "signup.html").read_text(encoding="utf-8")
    normalized_source = " ".join(source.split())

    assert PRODUCT_UPDATES_CONSENT_TEXT_BY_VERSION[CURRENT_PRODUCT_UPDATES_CONSENT_VERSION] in normalized_source
    assert "versão do consentimento" not in source.lower()
    assert "consentimento legado" not in source.lower()


def test_privacy_policy_explains_marketing_and_partner_offers() -> None:
    source = (FRONTEND_DIR / "politica-de-privacidade.html").read_text(encoding="utf-8")

    assert "ofertas de produtos e serviços de parceiros" in source
    assert "não compartilhamos seu endereço de e-mail com esses parceiros" in source
