"""Source column selection and Excel presentation cannot change financial meaning."""
import os
import subprocess
import sys
from io import BytesIO
from pathlib import Path

import pytest
from openpyxl import Workbook

from app.application.errors import InvalidFileContentError
from app.application.parsers.csv import parse_csv_transactions
from app.application.parsers.xlsx import parse_xlsx_transactions


@pytest.mark.parametrize("seed", ["0", "1", "4"])
def test_csv_amount_precedes_gross_value_across_processes(seed):
    code = """
from app.application.parsers.csv import parse_csv_transactions
for raw in (
    b'date,description,amount,valor_bruto,documento\\n2025-11-03,PIX,90,100,001103\\n',
    b'documento,valor_bruto,amount,description,date\\n001103,100,90,PIX,2025-11-03\\n',
):
    row = parse_csv_transactions(raw)[0]
    assert row.amount == 90, row
    assert row.document_number == '001103', row
"""
    result = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True,
                            cwd=Path(__file__).resolve().parents[1], env={**os.environ, "PYTHONHASHSEED": seed}, timeout=30)
    assert result.returncode == 0, result.stderr


def test_csv_without_canonical_amount_rejects_ambiguous_financial_aliases():
    with pytest.raises(InvalidFileContentError, match="ambiguous"):
        parse_csv_transactions(b"date,description,valor,value,documento\n2025-11-03,PIX,90,100,001103\n")


@pytest.mark.parametrize(("value", "number_format", "expected"), [
    (1103, "000000", "001103"), (0, "000000", "000000"),
    (1103, "General", "1103"), ("001103", "General", "001103"),
    (1103, "0.00", "1103"), (1234567, "000000", "1234567"),
])
def test_excel_document_uses_explicit_zero_padding_without_touching_amount(value, number_format, expected):
    book = Workbook()
    sheet = book.active
    sheet.append(["Extrato sintético"])
    sheet.append(["Data", "Historico", "Valor", "Documento"])
    sheet.append(["2025-11-03", "PIX", 90, value])
    sheet["D3"].number_format = number_format
    sheet["C3"].number_format = "000000"
    sheet.append([None, None, None, None])
    sheet.append(["2025-11-04", "TED", -20, "000009"])
    output = BytesIO()
    book.save(output)
    book.close()
    rows = parse_xlsx_transactions(output.getvalue())
    assert [row.amount for row in rows] == [90, -20]
    assert [row.document_number for row in rows] == [expected, "000009"]


@pytest.mark.real_http
def test_correct_amount_and_formatted_document_reach_preview_and_exports(tmp_path):
    from tests.ocr_conversion_http_helpers import public_client

    book = Workbook()
    book.active.append(["Data", "Historico", "Valor", "Documento"])
    book.active.append(["2025-11-03", "PIX", 90, 1103])
    book.active["D2"].number_format = "000000"
    output = BytesIO()
    book.save(output)
    book.close()
    inputs = [
        ("csv", b"date,description,amount,valor_bruto,documento\n2025-11-03,PIX,90,100,001103\n"),
        ("xlsx", output.getvalue()),
    ]
    with public_client(tmp_path) as client:
        owner = {"anonymous_fingerprint": "fp-reviewed-document"}
        for extension, raw in inputs:
            response = client.post("/convert", data=owner, files={"file": ("source."+extension, raw)})
            assert response.status_code == 200, response.text
            row = response.json()["analysis"]["preview_transactions"][0]
            assert row["amount"] == 90 and row["document_number"] == "001103"
            identifier = response.json()["processing_id"]
            assert client.get("/report/"+identifier, params=owner).status_code == 200
            exported = client.get("/convert-report/"+identifier, params={**owner, "format": "ofx"})
            assert exported.status_code == 200
            assert "<TRNAMT>90.00" in exported.text and "<CHECKNUM>001103" in exported.text
