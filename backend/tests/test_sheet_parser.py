from io import BytesIO

import pytest
from openpyxl import Workbook

from app.application.errors import InvalidFileContentError
from app.application.sheet_parser import parse_operational_sheet_rows


def _build_xlsx_bytes(rows: list[list[object]]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    for row in rows:
        sheet.append(row)
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def test_parse_operational_sheet_rows_csv_with_aliases() -> None:
    raw = (
        "dt_lancamento;vlr;historico\n"
        "01/04/2026;1200,00;RECEBIMENTO CLIENTE\n"
        "02/04/2026;-250,50;PAGAMENTO FORNECEDOR\n"
    ).encode("utf-8")

    parsed = parse_operational_sheet_rows(filename="sheet.csv", raw_bytes=raw)
    rows = parsed.rows

    assert len(rows) == 2
    assert rows[0].date == "2026-04-01"
    assert rows[0].amount == 1200.00
    assert rows[0].description == "RECEBIMENTO CLIENTE"
    assert rows[1].amount == -250.50
    assert parsed.mapping_detected == {
        "date": "dt_lancamento",
        "amount": "vlr",
        "description": "historico",
    }


def test_parse_operational_sheet_rows_xlsx_with_aliases() -> None:
    raw = _build_xlsx_bytes(
        [
            ["data", "valor_liquido", "descricao"],
            ["2026-04-01", 1200.00, "RECEBIMENTO CLIENTE"],
            ["2026-04-02", -250.50, "PAGAMENTO FORNECEDOR"],
        ]
    )

    parsed = parse_operational_sheet_rows(filename="sheet.xlsx", raw_bytes=raw)
    rows = parsed.rows

    assert len(rows) == 2
    assert rows[0].date == "2026-04-01"
    assert rows[0].amount == 1200.00
    assert rows[1].description == "PAGAMENTO FORNECEDOR"
    assert parsed.mapping_detected == {
        "date": "data",
        "amount": "valor_liquido",
        "description": "descricao",
    }


def test_parse_operational_sheet_rows_raises_for_missing_required_columns() -> None:
    raw = b"descricao,valor\nTESTE,100\n"

    with pytest.raises(InvalidFileContentError):
        parse_operational_sheet_rows(filename="sheet.csv", raw_bytes=raw)


def test_parse_operational_sheet_rows_raises_for_ambiguous_columns() -> None:
    raw = (
        "data,descricao,historico,valor\n"
        "2026-04-01,RECEBIMENTO A,RECEBIMENTO A,100\n"
    ).encode("utf-8")

    with pytest.raises(InvalidFileContentError, match="ambiguous column mapping"):
        parse_operational_sheet_rows(filename="sheet.csv", raw_bytes=raw)


def test_parse_operational_sheet_rows_accepts_dirty_headers() -> None:
    raw = (
        " Data Pgto ; Valor Liquido (R$) ; Historico Lanc. \n"
        "01/04/2026;1.200,00;RECEBIMENTO CLIENTE\n"
    ).encode("utf-8")

    parsed = parse_operational_sheet_rows(filename="sheet.csv", raw_bytes=raw)

    assert len(parsed.rows) == 1
    assert parsed.rows[0].date == "2026-04-01"
    assert parsed.rows[0].amount == 1200.00
    assert parsed.mapping_detected["description"] == "Historico Lanc."
