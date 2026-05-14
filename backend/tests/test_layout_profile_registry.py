from app.application.layout_profiles.registry import get_layout_profile, load_layout_profiles


def test_load_layout_profiles_from_versioned_models() -> None:
    profiles = load_layout_profiles()

    assert len(profiles) >= 58
    assert any(profile.profile_name == "c6_bank_extrato_mensal_tabela_tipo_descricao_valor_v1" for profile in profiles)
    assert all(profile.required_keywords for profile in profiles)
    assert all(0.0 < profile.min_score_hint <= 1.0 for profile in profiles)


def test_load_layout_profile_table_detection_metadata() -> None:
    profile = get_layout_profile("viacredi_ailos_extrato_conta_corrente_v1")

    assert profile is not None
    assert profile.expected_column_order == ("date", "description", "document", "credit", "debit", "balance")
    assert "CREDITO (R$)" in profile.column_aliases["credit"]
    assert "Credito" in profile.column_aliases["credit"]
    assert "SALDO (R$)" in profile.column_aliases["balance"]
