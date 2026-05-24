from app.application.normalization.pdf_text_rules import (
    apply_sign_hints,
    section_hint,
    should_ignore_line,
    should_skip_transaction_description,
)


def test_section_hint_detects_inflow_and_outflow_sections() -> None:
    assert section_hint("TOTAL DE ENTRADAS") == "inflow"
    assert section_hint("TOTAL DE SAIDAS") == "outflow"
    assert section_hint("SEM RESUMO") is None


def test_should_ignore_line_matches_known_headers() -> None:
    assert should_ignore_line("SALDO INICIAL DO DIA")
    assert should_ignore_line("VALORES EM R$")
    assert should_ignore_line("")
    assert not should_ignore_line("PIX RECEBIDO CLIENTE")


def test_should_skip_transaction_description_filters_balance_and_noise() -> None:
    assert should_skip_transaction_description("SALDO DO DIA")
    assert should_skip_transaction_description("LIMITE DA CONTA")
    assert should_skip_transaction_description("SALDO EM CONTA")
    assert should_skip_transaction_description("Agência | Conta Total Disponível (R$) Total (R$) 1234 | 56789-0 12609,62")
    assert should_skip_transaction_description("bradesco net empresa Extrato Mensal / Por Período Data da operação")
    assert should_skip_transaction_description("ata da operação: 15/04/2024 - 09h15")
    assert not should_skip_transaction_description("TRANSFERENCIA RECEBIDA PIX")


def test_apply_sign_hints_prioritizes_description_and_section_hints() -> None:
    assert apply_sign_hints(10.0, "TRANSFERENCIA RECEBIDA", None) == 10.0
    assert apply_sign_hints(10.0, "PAGAMENTO CARTAO", None) == -10.0
    assert apply_sign_hints(-10.0, "GENERICA", "inflow") == 10.0
    assert apply_sign_hints(10.0, "GENERICA", "outflow") == -10.0
