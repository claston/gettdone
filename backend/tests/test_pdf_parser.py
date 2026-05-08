from app.application import pdf_parser as pdf_parser_module
from app.application.pdf_parser import parse_pdf_transactions


def test_parse_pdf_transactions_handles_inline_and_multiline_amount_rows(monkeypatch) -> None:
    page_text = "\n".join(
        [
            "TRANSAÇÕES DE 08 MAR A 08 ABR",
            "16 MAR 2026 Pagamento em 16 MAR −R$ 240,24",
            "25 MAR 2026",
            "Compra AGIR CONTABILIDADE E ASSESSORIA LTDA",
            "R$ 241,05",
        ]
    )
    monkeypatch.setattr(pdf_parser_module, "_extract_pdf_page_texts", lambda raw_bytes: [page_text])

    result = parse_pdf_transactions(b"%PDF synthetic")

    assert len(result.transactions) == 2
    assert result.parse_metrics["selected_parser"] == "grouped"
    assert result.transactions[0].date == "2026-03-16"
    assert result.transactions[0].amount == -240.24
    assert result.transactions[1].date == "2026-03-25"
    assert result.transactions[1].amount == -241.05


def test_parse_pdf_transactions_parses_unicode_minus_with_currency_prefix(monkeypatch) -> None:
    page_text = "10 ABR 2026 Ajuste manual −R$ 10,00"
    monkeypatch.setattr(pdf_parser_module, "_extract_pdf_page_texts", lambda raw_bytes: [page_text])

    result = parse_pdf_transactions(b"%PDF synthetic")

    assert len(result.transactions) == 1
    assert result.transactions[0].date == "2026-04-10"
    assert result.transactions[0].amount == -10.0
