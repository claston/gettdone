from app.application.layout_profiles.registry import get_layout_profile, load_layout_profiles


def test_load_layout_profiles_from_versioned_models() -> None:
    profiles = load_layout_profiles()

    assert len(profiles) >= 58
    assert any(profile.profile_name == "c6_bank_extrato_mensal_tabela_tipo_descricao_valor_v1" for profile in profiles)
    assert all(profile.required_keywords for profile in profiles)
    assert all(profile.layout_family for profile in profiles)
    assert all(profile.statement_type for profile in profiles)
    assert all(0.0 < profile.min_score_hint <= 1.0 for profile in profiles)


def test_load_layout_profile_table_detection_metadata() -> None:
    profile = get_layout_profile("viacredi_ailos_extrato_conta_corrente_v1")

    assert profile is not None
    assert profile.layout_family == "viacredi_ailos_extrato_tabela_credito_debito"
    assert profile.statement_type == "conta_corrente_extrato"
    assert profile.expected_column_order == ("date", "description", "document", "credit", "debit", "balance")
    assert "CREDITO (R$)" in profile.column_aliases["credit"]
    assert "Credito" in profile.column_aliases["credit"]
    assert "SALDO (R$)" in profile.column_aliases["balance"]


def test_load_layout_profile_exposes_non_statement_document_types() -> None:
    receipt = get_layout_profile("banrisul_recibo_pagamento_v1")
    credit_card_bill = get_layout_profile("banco_inter_fatura_cartao_despesas_v1")

    assert receipt is not None
    assert receipt.layout_family == "banrisul_recibo_pagamento"
    assert receipt.statement_type == "comprovante_pagamento"

    assert credit_card_bill is not None
    assert credit_card_bill.layout_family == "banco_inter_fatura_cartao_despesas"
    assert credit_card_bill.statement_type == "cartao_credito_fatura"


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


def test_load_caixa_gerenciador_pagamentos_efetuados_profile() -> None:
    profile = get_layout_profile("caixa_gerenciador_pagamentos_efetuados_boleto_v1")

    assert profile is not None
    assert profile.bank == "Caixa Economica Federal"
    assert profile.layout_family == "caixa_gerenciador_pagamentos_efetuados_grouped_list"
    assert profile.statement_type == "pagamentos_efetuados"
    assert profile.confidence_label == "medium"
    assert profile.schema_version == 2
    assert profile.parsing.date_formats == ("MMM dd, yyyy", "MMM dd, yy")
    assert profile.parsing.month_language == "en-US"
    assert profile.parsing.negative_patterns == ("-{amount}", "-R$ {amount}", "R$ -{amount}")
    assert profile.expected_column_order == ("date_group", "time", "payment_type", "counterparty", "status", "amount")
