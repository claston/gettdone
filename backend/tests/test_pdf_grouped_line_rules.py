from app.application.normalization.pdf_grouped_line_rules import should_ignore_grouped_line


def test_should_ignore_grouped_line_for_page_counter() -> None:
    assert should_ignore_grouped_line("2 DE 5") is True


def test_should_ignore_grouped_line_for_known_non_transaction_headers() -> None:
    assert should_ignore_grouped_line("SALDO INICIAL DO DIA") is True


def test_should_ignore_grouped_line_for_mobile_arrow_icons() -> None:
    assert should_ignore_grouped_line("↑") is True
    assert should_ignore_grouped_line("↓") is True
    assert should_ignore_grouped_line("?") is True


def test_should_ignore_grouped_line_for_daily_balance_context() -> None:
    assert should_ignore_grouped_line("SALDO DO DIA") is True


def test_should_ignore_grouped_line_keeps_regular_description() -> None:
    assert should_ignore_grouped_line("PAGAMENTO BOLETO ENERGIA") is False
