from app.application.layout_profiles.registry import load_layout_profiles


def test_load_layout_profiles_from_versioned_models() -> None:
    profiles = load_layout_profiles()

    assert len(profiles) >= 58
    assert any(profile.profile_name == "c6_bank_extrato_mensal_tabela_tipo_descricao_valor_v1" for profile in profiles)
    assert all(profile.required_keywords for profile in profiles)
    assert all(0.0 < profile.min_score_hint <= 1.0 for profile in profiles)
