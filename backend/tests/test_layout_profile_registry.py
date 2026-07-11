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


def test_load_layout_profile_v2_executable_parsing_rules() -> None:
    profile = get_layout_profile("banco_do_nordeste_extrato_periodo_a4_v1")

    assert profile is not None
    assert profile.schema_version == 2
    assert profile.parsing.date_formats == ("dd/MM/yyyy", "dd/MM/yy", "dd/MM", "ddMMyyyy")
    assert profile.parsing.amount_locale == "pt-BR"
    assert "Detalhamento do Extrato" in profile.parsing.ignore_rows
    assert profile.parsing.opening_balance_rows == ("Saldo Anterior",)
    assert profile.parsing.opening_balance_policy == "import"
    assert "{amount} DB" in profile.parsing.negative_patterns
    assert "{amount} CR" in profile.parsing.positive_patterns


def test_load_migrated_itau_and_caixa_v2_parsing_rules() -> None:
    itau = get_layout_profile("itau_empresas_extrato_lancamentos_conta_corrente_v1")
    caixa = get_layout_profile("caixa_siatr_saldos_lancamentos_a4_v1")

    assert itau is not None
    assert itau.schema_version == 2
    assert itau.parsing.date_formats == ("dd/MMM", "ddMMM")
    assert itau.parsing.ignore_rows == ("SALDO DO DIA",)
    assert itau.parsing.opening_balance_policy == "skip"

    assert caixa is not None
    assert caixa.schema_version == 2
    assert caixa.parsing.date_formats == ("dd/MM/yy", "dd/MM/yyyy", "ddMMyy")
    assert caixa.parsing.ignore_rows == ("SALDO DIA",)
    assert caixa.parsing.opening_balance_policy == "import"
