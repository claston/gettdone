from app.application.bank_resolver import resolve_bank_code


def test_resolve_bank_code_from_layout_profile_bank_name() -> None:
    assert resolve_bank_code(layout_inference_name="banco_do_brasil_extrato_conta_corrente_lancamentos_v1") == "001"


def test_resolve_bank_code_uses_override_when_provided() -> None:
    assert (
        resolve_bank_code(
            layout_inference_name="banco_do_brasil_extrato_conta_corrente_lancamentos_v1",
            bank_code_override="237",
        )
        == "237"
    )


def test_resolve_bank_code_falls_back_to_default_for_unknown_layout() -> None:
    assert resolve_bank_code(layout_inference_name="layout_inexistente_teste") == "000"


def test_resolve_bank_code_from_stone_layout_profile() -> None:
    assert resolve_bank_code(layout_inference_name="stone_extrato_conta_corrente_a4_v1") == "197"


def test_resolve_bank_code_from_stone_grouped_layout_profile() -> None:
    assert resolve_bank_code(layout_inference_name="stone_extrato_conta_corrente_lancamento_valor_saldo_v1") == "197"


def test_resolve_bank_code_from_bradesco_unificado_poupanca_layout_profile() -> None:
    assert resolve_bank_code(layout_inference_name="bradesco_extrato_unificado_pj_poupanca_facil_a4_v1") == "237"


def test_resolve_bank_code_from_banco_nordeste_periodo_a4_layout_profile() -> None:
    assert resolve_bank_code(layout_inference_name="banco_do_nordeste_extrato_periodo_a4_v1") == "004"


def test_resolve_bank_code_from_banco_nordeste_fundos_rentabilidade_layout_profile() -> None:
    assert resolve_bank_code(layout_inference_name="banco_do_nordeste_fundos_investimentos_rentabilidade_v1") == "004"
