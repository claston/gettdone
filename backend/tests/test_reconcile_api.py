from io import BytesIO

from fastapi.testclient import TestClient
from openpyxl import Workbook

from app.main import app


def _build_xlsx_bytes(rows: list[list[object]]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    for row in rows:
        sheet.append(row)
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def test_reconcile_happy_path_accepts_bank_and_sheet_files() -> None:
    client = TestClient(app)

    response = client.post(
        "/reconcile",
        files={
            "bank_file": ("bank.csv", b"date,description,amount\n2026-04-01,teste,-100", "text/csv"),
            "sheet_file": ("sheet.csv", b"data,valor,descricao\n2026-04-01,100,TEST", "text/csv"),
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "accepted"
    assert payload["bank_filename"] == "bank.csv"
    assert payload["bank_file_type"] == "csv"
    assert payload["sheet_filename"] == "sheet.csv"
    assert payload["sheet_file_type"] == "csv"
    assert payload["bank_rows_parsed"] == 1
    assert payload["sheet_rows_parsed"] == 1
    assert payload["sheet_mapping_detected"] == {
        "date": "data",
        "amount": "valor",
        "description": "descricao",
    }
    assert len(payload["normalization_preview"]) == 2
    assert payload["normalization_preview"][0]["source"] == "bank"
    assert payload["normalization_preview"][1]["source"] == "sheet"


def test_reconcile_rejects_unsupported_bank_file_type() -> None:
    client = TestClient(app)

    response = client.post(
        "/reconcile",
        files={
            "bank_file": ("bank.pdf", b"%PDF", "application/pdf"),
            "sheet_file": ("sheet.xlsx", b"fake-xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Unsupported bank file type. Use CSV, XLSX, or OFX."


def test_reconcile_rejects_unsupported_sheet_file_type() -> None:
    client = TestClient(app)

    response = client.post(
        "/reconcile",
        files={
            "bank_file": ("bank.csv", b"date,description,amount\n2026-04-01,TEST,10", "text/csv"),
            "sheet_file": ("sheet.ofx", b"<OFX>...</OFX>", "application/octet-stream"),
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Unsupported sheet file type. Use CSV or XLSX."


def test_reconcile_returns_422_when_sheet_is_missing_required_columns() -> None:
    client = TestClient(app)

    response = client.post(
        "/reconcile",
        files={
            "bank_file": ("bank.csv", b"date,description,amount\n2026-04-01,TEST,10", "text/csv"),
            "sheet_file": ("sheet.csv", b"descricao,valor\nTEST,100", "text/csv"),
        },
    )

    assert response.status_code == 422
    assert "missing required columns" in response.json()["detail"]


def test_reconcile_accepts_sheet_xlsx_with_alias_columns() -> None:
    client = TestClient(app)
    sheet_raw = _build_xlsx_bytes(
        [
            ["dt_lancamento", "vlr", "historico"],
            ["01/04/2026", "1200,00", "RECEBIMENTO CLIENTE"],
            ["02/04/2026", "-250,50", "PAGAMENTO FORNECEDOR"],
        ]
    )

    response = client.post(
        "/reconcile",
        files={
            "bank_file": ("bank.csv", b"date,description,amount\n2026-04-01,TEST,10", "text/csv"),
            "sheet_file": (
                "sheet.xlsx",
                sheet_raw,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ),
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["sheet_file_type"] == "xlsx"
    assert payload["bank_rows_parsed"] == 1
    assert payload["sheet_rows_parsed"] == 2
    assert payload["sheet_mapping_detected"] == {
        "date": "dt_lancamento",
        "amount": "vlr",
        "description": "historico",
    }
    assert len(payload["normalization_preview"]) == 3


def test_reconcile_returns_422_for_ambiguous_sheet_mapping() -> None:
    client = TestClient(app)

    response = client.post(
        "/reconcile",
        files={
            "bank_file": ("bank.csv", b"date,description,amount\n2026-04-01,TEST,10", "text/csv"),
            "sheet_file": (
                "sheet.csv",
                b"data,descricao,historico,valor\n2026-04-01,RECEBIMENTO,RECEBIMENTO,100",
                "text/csv",
            ),
        },
    )

    assert response.status_code == 422
    assert "ambiguous column mapping" in response.json()["detail"]


def test_reconcile_normalization_preview_aligns_sign_with_same_semantic_description() -> None:
    client = TestClient(app)

    response = client.post(
        "/reconcile",
        files={
            "bank_file": (
                "bank.csv",
                b"date,description,amount\n01/04/2026,pagamento fornecedor alfa,980.00",
                "text/csv",
            ),
            "sheet_file": (
                "sheet.csv",
                b"data,valor,descricao\n2026-04-01,-980.00,PAGAMENTO FORNECEDOR ALFA",
                "text/csv",
            ),
        },
    )

    assert response.status_code == 200
    payload = response.json()
    preview = payload["normalization_preview"]
    assert preview[0]["source"] == "bank"
    assert preview[1]["source"] == "sheet"
    assert preview[0]["amount"] == -980.0
    assert preview[1]["amount"] == -980.0
    assert preview[0]["type"] == "outflow"
    assert preview[1]["type"] == "outflow"
