from __future__ import annotations

from app.application.bank_catalog import normalize_bank_code, resolve_bank_code_from_name
from app.application.bank_identity import VERIFIED_STATEMENT_BANKS
from app.application.layout_profiles.registry import get_layout_profile

DEFAULT_BANK_CODE = "000"


def resolve_bank_code(
    *,
    bank_code_override: str | None = None,
    layout_inference_name: str | None = None,
) -> str:
    override_code = normalize_bank_code(bank_code_override)
    if override_code is not None:
        return override_code

    profile = get_layout_profile(layout_inference_name)
    if profile is not None:
        resolved = resolve_bank_code_from_name(profile.bank)
        if resolved is not None:
            return resolved

    return resolve_bank_code_from_name(VERIFIED_STATEMENT_BANKS.get(str(layout_inference_name or ""))) or DEFAULT_BANK_CODE
