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
