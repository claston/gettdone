import pytest

from app.application.csv_parser import parse_csv_transactions
from app.application.errors import InvalidFileContentError


def test_parse_csv_transactions_with_comma_delimiter() -> None:
    raw = b"date,description,amount\n2026-04-01,IFOOD,-58.90\n2026-04-02,SALARIO,2500.00\n"

    rows = parse_csv_transactions(raw)

    assert len(rows) == 2
    assert rows[0].date == "2026-04-01"
    assert rows[0].description == "IFOOD"
    assert rows[0].amount == -58.90
    assert rows[0].type == "outflow"
    assert rows[1].type == "inflow"


def test_parse_csv_transactions_with_semicolon_and_pt_br_formats() -> None:
    raw = (
        "data;descricao;valor\n"
        "01/04/2026;MERCADO;-120,50\n"
        "02/04/2026;PIX RECEBIDO;1.350,00\n"
    ).encode("utf-8")

    rows = parse_csv_transactions(raw)

    assert len(rows) == 2
    assert rows[0].date == "2026-04-01"
    assert rows[0].amount == -120.50
    assert rows[1].amount == 1350.00
    assert rows[1].type == "inflow"


def test_parse_csv_transactions_raises_for_missing_required_columns() -> None:
    raw = b"date,description\n2026-04-01,Missing amount\n"

    with pytest.raises(InvalidFileContentError):
        parse_csv_transactions(raw)
