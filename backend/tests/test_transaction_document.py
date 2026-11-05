"""Document references remain text, optional and independent of transaction identity."""
import csv
import re
from dataclasses import replace
from io import BytesIO, StringIO

import pytest
from openpyxl import Workbook, load_workbook

from app.application.default_conversion_pipeline import build_default_conversion_pipeline
from app.application.models import NormalizedTransaction
from app.application.ofx_writer import build_ofx_statement
from app.application.parsers.ofx import parse_ofx_transactions
from app.application.storage_service import TempAnalysisStorage
from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf
from tests.ocr_conversion_http_helpers import public_client


def input_file(kind):
    if kind == "pdf":
        return generate_native_text_pdf(((
            "Extrato de Conta Corrente", "Data Origem Historico Documento Lote Valor R$",
            "03/11/2025 1234-5 Pix - Enviado 001103 13105 20,00 D",
            "03/11/2025 1234-5 Pix - Enviado 001103 13105 20,00 D",
            "03/11/2025 1234-5 Saldo do dia 13105 500,00 C", "Conta corrente + Rende Facil",
            "bb.com.br - Central de Atendimento BB",
        ),))
    if kind == "csv":
        return b"date,description,amount,documento\n2025-11-03,PIX,-20,001103\n2025-11-03,PIX,-20,001103\n"
    if kind == "xlsx":
        book = Workbook()
        book.active.append(["Data", "Historico", "Valor", "Documento"])
        for _ in range(2):
            book.active.append(["2025-11-03", "PIX", -20, "001103"])
        stream = BytesIO()
        book.save(stream)
        return stream.getvalue()
    return ("<OFX>" + "".join(
        f"<STMTTRN><DTPOSTED>20251103<TRNAMT>-20<FITID>source-{i}<CHECKNUM>001103<MEMO>PIX</STMTTRN>"
        for i in range(2)) + "</OFX>").encode()


@pytest.mark.parametrize("kind", ["pdf", "csv", "xlsx", "ofx"])
def test_document_survives_pipeline_storage_and_all_exports(tmp_path, monkeypatch, kind):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    result = build_default_conversion_pipeline().run(
        filename="synthetic."+kind, raw_bytes=input_file(kind), analysis_id="document-test")
    data = result.analysis_data
    assert len(data.report_transactions) == 2
    assert [row.document_number for row in data.report_transactions] == ["001103", "001103"]
    assert all("001103" not in row.description for row in data.report_transactions)
    storage = TempAnalysisStorage(tmp_path)
    storage.save_analysis(data)
    rows = list(csv.DictReader(StringIO(storage.get_convert_report_path(data.analysis_id, "csv").read_text(encoding="utf-8"))))
    assert [row["document_number"] for row in rows] == ["001103", "001103"]
    for path in [storage.get_report_path(data.analysis_id), storage.get_convert_report_path(data.analysis_id, "xlsx")]:
        book = load_workbook(path, read_only=True)
        values = list(book.worksheets[0].values)
        index = next(i for i, value in enumerate(values[0]) if value in {"Documento", "document_number"})
        assert [row[index] for row in values[1:]] == ["001103", "001103"]
        book.close()
    exported = storage.get_convert_report_path(data.analysis_id, "ofx").read_text(encoding="utf-8")
    assert exported.count("<CHECKNUM>001103") == 2
    assert len(set(re.findall(r"<FITID>([^\n<]+)", exported))) == 2
    assert [row.document_number for row in parse_ofx_transactions(exported.encode())] == ["001103", "001103"]


def test_document_does_not_change_fitid_and_long_values_are_not_truncated():
    transaction = NormalizedTransaction("2025-11-03", "TESTE", -20, "outflow")
    base = build_ofx_statement([transaction])
    for document in ["001103", "123456789012345678901234567890123456789", "ABC-001"]:
        result = build_ofx_statement([replace(transaction, document_number=document)])
        assert re.findall(r"<FITID>([^\n<]+)", result) == re.findall(r"<FITID>([^\n<]+)", base)
        assert document in result
    assert "<CHECKNUM>" not in base and "<REFNUM>" not in base


def test_explicit_ofx_reference_keeps_its_meaning():
    raw = b"<STMTTRN><DTPOSTED>20251103<TRNAMT>-20<REFNUM>000123456789012345<MEMO>TESTE</STMTTRN>"
    parsed = parse_ofx_transactions(raw)
    assert parsed[0].document_number == "000123456789012345"
    exported = build_ofx_statement(parsed)
    assert "<REFNUM>000123456789012345" in exported
    assert "<CHECKNUM>" not in exported


@pytest.mark.real_http
def test_document_survives_api_edits_and_legacy_documents(tmp_path):
    with public_client(tmp_path) as client:
        owner = {"anonymous_fingerprint": "fp-document"}
        response = client.post("/convert", data=owner, files={"file": ("synthetic.csv", input_file("csv"), "text/csv")})
        assert response.status_code == 200, response.text
        data = response.json()["analysis"]
        assert [row["document_number"] for row in data["preview_transactions"]] == ["001103", "001103"]
        identifier = response.json()["processing_id"]
        assert client.get("/report/"+identifier, params=owner).status_code == 200
        edit = client.post("/convert-edits/"+identifier, params=owner, json={"edits": [
            {"row_id": data["preview_transactions"][0]["transaction_id"], "date": "2025-11-03", "description": "EDITADO", "debit": 20},
        ]})
        assert edit.status_code == 200, edit.text
        assert edit.json()["preview_transactions"][0]["document_number"] == "001103"
        exported = client.get("/convert-report/"+identifier, params={**owner, "format": "ofx"})
        assert exported.status_code == 200
        assert exported.text.count("<CHECKNUM>001103") == 2
        legacy = client.post("/convert", data=owner, files={
            "file": ("legacy.csv", b"date,description,amount\n2025-11-03,TESTE,-20\n", "text/csv"),
        })
        assert legacy.status_code == 200
        assert legacy.json()["analysis"]["preview_transactions"][0]["document_number"] is None
        assert client.post("/analyze").status_code == 405
        assert client.get("/report/missing-document", params=owner).status_code == 404
