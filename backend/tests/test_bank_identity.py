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


def test_resolve_bank_name_from_unlisted_cooperative_header() -> None:
    text = """
    COOPERATIVA DE CREDITO VALE VERDE
    Extrato de conta corrente
    Cliente Maria Silva
    """

    assert (
        resolve_bank_name(layout_inference_name="generic_statement_ptbr", extracted_text=text)
        == "COOPERATIVA DE CREDITO VALE VERDE"
    )


def test_unknown_bank_header_does_not_capture_holder_or_account_data() -> None:
    text = """
    BANCO VALE SEGURO S.A. CNPJ 12.345.678/0001-90
    Cliente Maria Silva
    Agencia 1234 Conta 98765-4
    Extrato de conta corrente
    """

    bank_name = resolve_bank_name(layout_inference_name="generic_statement_ptbr", extracted_text=text)

    assert bank_name == "BANCO VALE SEGURO S.A"
    assert "MARIA" not in bank_name
    assert "123" not in bank_name


def test_person_name_without_institutional_context_is_not_a_bank() -> None:
    text = """
    Cliente Maria Silva
    Extrato de conta corrente
    Agencia 1234 Conta 98765-4
    """

    assert resolve_bank_name(layout_inference_name="generic_statement_ptbr", extracted_text=text) is None


def test_unlisted_header_takes_priority_over_bank_mentioned_in_transaction() -> None:
    text = """
    COOPERATIVA DE CREDITO VALE VERDE
    Extrato de conta corrente
    Data Historico Valor
    10/09/2026 PIX PARA CLIENTE BANCO SANTANDER 100,00
    """

    assert (
        resolve_bank_name(layout_inference_name="generic_statement_ptbr", extracted_text=text)
        == "COOPERATIVA DE CREDITO VALE VERDE"
    )


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
