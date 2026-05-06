import pytest

from app.application.errors import InvalidFileContentError
from app.application.pdf_parser import _extract_pdf_page_texts, parse_pdf_transactions


def test_parse_pdf_transactions_with_grouped_layout(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.application import pdf_parser

    grouped_text = """
    06 NOV 2023 Total de entradas + 1.069,04
    Transferencia recebida pelo Pix CLIENTE A
    1.069,04
    14 NOV 2023 Total de saidas - 4.000,00
    Transferencia enviada pelo Pix FORNECEDOR B
    4.000,00
    """
    monkeypatch.setattr(pdf_parser, "_extract_pdf_page_texts", lambda raw_bytes: [grouped_text])

    result = parse_pdf_transactions(b"%PDF synthetic grouped")

    assert result.layout.layout_name in {"nubank_statement_ptbr", "generic_statement_ptbr"}
    assert len(result.transactions) == 2
    assert any(item.amount > 0 for item in result.transactions)
    assert any(item.amount < 0 for item in result.transactions)
    assert result.parse_metrics["selected_parser"] == "grouped"
    assert result.parse_metrics["grouped_transactions_count"] == 2
    assert result.parse_metrics["inline_transactions_count"] == 0


def test_parse_pdf_transactions_grouped_day_month_without_year(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.application import pdf_parser

    grouped_text = """
    FATURA 15 OUT 2023
    TRANSACOES
    DE 08 SET A 08 OUT
    14 SET
    Pagamento em 14 SET
    563,69
    20 SET
    IOF de "Voluum.Com"
    21,47
    20 SET
    Voluum.Com
    399,09
    """
    monkeypatch.setattr(pdf_parser, "_extract_pdf_page_texts", lambda raw_bytes: [grouped_text])

    result = parse_pdf_transactions(b"%PDF synthetic grouped month-only date")

    assert result.parse_metrics["selected_parser"] == "grouped"
    assert len(result.transactions) == 3
    assert result.transactions[0].date == "2023-09-14"
    assert result.transactions[0].amount == -563.69
    assert result.transactions[1].date == "2023-09-20"
    assert result.transactions[2].date == "2023-09-20"


def test_parse_pdf_transactions_with_itau_inline_layout(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.application import pdf_parser

    inline_text = """
    extrato conta / lancamentos
    data lancamentos valor (R$) saldo (R$)
    13/04/2026 PIX TRANSF ERICA S13/04 -2.835,00
    09/04/2026 TED 102.0001.ERICA S Y 6.000,00
    09/04/2026 SALDO DO DIA -1.307,48
    """
    monkeypatch.setattr(pdf_parser, "_extract_pdf_page_texts", lambda raw_bytes: [inline_text])

    result = parse_pdf_transactions(b"%PDF synthetic inline")

    assert result.layout.layout_name in {"itau_statement_ptbr", "generic_statement_ptbr"}
    assert len(result.transactions) == 2
    assert all("SALDO DO DIA" not in item.description.upper() for item in result.transactions)
    assert any(item.amount > 0 for item in result.transactions)
    assert any(item.amount < 0 for item in result.transactions)
    assert result.parse_metrics["selected_parser"] == "inline"
    assert result.parse_metrics["inline_candidates_count"] >= 1
    assert result.parse_metrics["inline_transactions_count"] == 2


def test_parse_pdf_transactions_with_tabular_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.application import pdf_parser

    tabular_text = """
    extrato conta / lancamentos
    data lancamentos valor (R$) saldo (R$)
    13/04/2026 PIX TRANSF ERICA S13/04 -2.835,00 -4.142,48
    09/04/2026 TED 102.0001.ERICA S Y 6.000,00 1.857,52
    09/04/2026 SALDO DO DIA -1.307,48
    """
    monkeypatch.setattr(pdf_parser, "_extract_pdf_page_texts", lambda raw_bytes: [tabular_text])

    result = parse_pdf_transactions(b"%PDF synthetic tabular fallback")

    assert result.layout.layout_name in {"itau_statement_ptbr", "generic_statement_ptbr"}
    assert result.parse_metrics["selected_parser"] == "tabular"
    assert len(result.transactions) == 2
    assert all("SALDO DO DIA" not in item.description.upper() for item in result.transactions)
    assert result.transactions[0].amount == -2835.0
    assert result.transactions[1].amount == 6000.0


def test_parse_pdf_transactions_inline_short_date_and_trailing_minus(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.application import pdf_parser

    inline_text = """
    extrato conta / lancamentos
    periodo de visualizacao: 14/03/2026 ate 13/04/2026
    13/04 PIX TRANSF LOJA X 1.250,00-
    10/04 TED RECEBIDA CLIENTE Y 800,00
    """
    monkeypatch.setattr(pdf_parser, "_extract_pdf_page_texts", lambda raw_bytes: [inline_text])

    result = parse_pdf_transactions(b"%PDF synthetic inline short date")

    assert result.parse_metrics["selected_parser"] == "inline"
    assert len(result.transactions) == 2
    assert result.transactions[0].date == "2026-04-13"
    assert result.transactions[0].amount == -1250.0
    assert result.transactions[1].date == "2026-04-10"
    assert result.transactions[1].amount == 800.0


def test_parse_pdf_transactions_tabular_short_date_with_currency_symbol(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.application import pdf_parser

    tabular_text = """
    periodo: 01/04/2026 a 30/04/2026
    13/04 PIX TRANSF ERICA S13/04 R$ 2.835,00- R$ -4.142,48
    09/04 TED 102.0001.ERICA S Y R$ 6.000,00 R$ 1.857,52
    """
    monkeypatch.setattr(pdf_parser, "_extract_pdf_page_texts", lambda raw_bytes: [tabular_text])

    result = parse_pdf_transactions(b"%PDF synthetic tabular short date")

    assert result.parse_metrics["selected_parser"] == "tabular"
    assert len(result.transactions) == 2
    assert result.transactions[0].date == "2026-04-13"
    assert result.transactions[0].amount == -2835.0
    assert result.transactions[1].date == "2026-04-09"
    assert result.transactions[1].amount == 6000.0


def test_parse_pdf_transactions_inline_month_abbrev_date(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.application import pdf_parser

    inline_text = """
    fatura cartao nubank
    periodo 01/03/2026 ate 31/03/2026
    13 MAR IFOOD *PEDIDO 57,90-
    10 MAR ESTORNO AJUSTE 12,00
    """
    monkeypatch.setattr(pdf_parser, "_extract_pdf_page_texts", lambda raw_bytes: [inline_text])

    result = parse_pdf_transactions(b"%PDF synthetic card month abbrev")

    assert result.parse_metrics["selected_parser"] == "inline"
    assert len(result.transactions) == 2
    assert result.transactions[0].date == "2026-03-13"
    assert result.transactions[0].amount == -57.9
    assert result.transactions[1].date == "2026-03-10"
    assert result.transactions[1].amount == 12.0


def test_parse_pdf_transactions_raises_when_no_transaction_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.application import pdf_parser

    monkeypatch.setattr(pdf_parser, "_extract_pdf_page_texts", lambda raw_bytes: ["SEM DADOS DE MOVIMENTACOES"])

    with pytest.raises(
        InvalidFileContentError,
        match="unsupported table layout|no recognizable transaction row pattern",
    ):
        parse_pdf_transactions(b"%PDF-1.4 fake")


def test_parse_pdf_transactions_with_columnar_table_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.application import pdf_parser

    columnar_text = """
    Extrato Bancario Simulado
    Data
    Descricao
    Tipo
    Valor (R$)
    Saldo (R$)
    01/03/2026
    Compra online
    Debito
    -115.37
    4884.63
    02/03/2026
    Transferencia recebida
    Credito
    +249.61
    5134.24
    03/03/2026
    Academia
    Debito
    91.35
    5042.89
    """
    monkeypatch.setattr(pdf_parser, "_extract_pdf_page_texts", lambda raw_bytes: [columnar_text])

    result = parse_pdf_transactions(b"%PDF synthetic columnar blocks")

    assert result.parse_metrics["selected_parser"] == "columnar"
    assert len(result.transactions) == 3
    assert result.transactions[0].date == "2026-03-01"
    assert result.transactions[0].amount == -115.37
    assert result.transactions[1].amount == 249.61
    assert result.transactions[2].amount == -91.35


def test_extract_pdf_page_texts_without_ocr_keeps_original_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.application import pdf_parser

    monkeypatch.setattr(pdf_parser, "_read_native_pdf_page_texts", lambda raw_bytes: [])
    monkeypatch.delenv("PDF_OCR_ENABLED", raising=False)

    with pytest.raises(InvalidFileContentError, match="does not contain extractable text"):
        _extract_pdf_page_texts(b"%PDF synthetic no text")


def test_extract_pdf_page_texts_uses_ocr_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.application import pdf_parser

    monkeypatch.setattr(pdf_parser, "_read_native_pdf_page_texts", lambda raw_bytes: [])
    monkeypatch.setattr(pdf_parser, "_extract_pdf_page_texts_with_ocr", lambda raw_bytes: ["OCR LINE 1", "OCR LINE 2"])
    monkeypatch.setenv("PDF_OCR_ENABLED", "true")

    pages = _extract_pdf_page_texts(b"%PDF synthetic no text")

    assert pages == ["OCR LINE 1", "OCR LINE 2"]


def test_extract_pdf_page_texts_with_ocr_enabled_and_missing_dependencies_raises_clear_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.application import pdf_parser

    monkeypatch.setattr(pdf_parser, "_read_native_pdf_page_texts", lambda raw_bytes: [])
    monkeypatch.setattr(
        pdf_parser,
        "_extract_pdf_page_texts_with_ocr",
        lambda raw_bytes: (_ for _ in ()).throw(InvalidFileContentError("OCR dependencies are not installed.")),
    )
    monkeypatch.setenv("PDF_OCR_ENABLED", "1")

    with pytest.raises(InvalidFileContentError, match="OCR dependencies are not installed"):
        _extract_pdf_page_texts(b"%PDF synthetic no text")
