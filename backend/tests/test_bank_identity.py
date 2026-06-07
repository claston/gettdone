from app.application.bank_identity import resolve_bank_name, resolve_conversion_model_label


def test_resolve_bank_name_from_legacy_layout_name() -> None:
    assert resolve_bank_name(layout_inference_name="itau_statement_ptbr") == "Itau"


def test_resolve_bank_name_from_pdf_text_when_layout_is_generic() -> None:
    text = """
    Banco Santander
    Extrato de conta corrente
    Agencia 1234
    """
    assert resolve_bank_name(layout_inference_name="generic_statement_ptbr", extracted_text=text) == "Santander"


def test_resolve_conversion_model_label_uses_bank_name_for_generic_layout() -> None:
    assert (
        resolve_conversion_model_label(
            layout_inference_name="generic_statement_ptbr",
            bank_name="Itau",
        )
        == "Nao identificado - Itau"
    )


def test_resolve_conversion_model_label_preserves_specific_layout_name() -> None:
    assert (
        resolve_conversion_model_label(
            layout_inference_name="itau_empresas_extrato_30_horas_tabela_v1",
            bank_name="Itau",
        )
        == "itau_empresas_extrato_30_horas_tabela_v1"
    )
