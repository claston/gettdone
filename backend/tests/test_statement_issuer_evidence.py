"""Issuer evidence and layout structure are independent decisions."""
import pytest

from app.application.bank_identity import resolve_bank_name, resolve_conversion_model_label
from app.application.default_conversion_pipeline import _resolve_inferred_bank_code

BODY = "Data Histórico Valor Saldo\n03/10/2026 Pix enviado Caixa Economica Federal -10,00 90,00"
LAYOUTS = ["caixa_app_extrato_ocr_sem_cabecalho_v1", "account_detail_descending_cash_v1", "generic_statement_ptbr"]


@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("issuer", ["Banco Santander", "EXTRATO DE CONTA — BANCO SANTANDER"])
def test_institutional_title_overrides_layout_bank_without_changing_model(layout, issuer):
    text = f"{issuer}\nAgência: 0001 Conta: 12345-6\n{BODY}"
    bank = resolve_bank_name(layout_inference_name=layout, extracted_text=text)
    assert bank == "Santander"
    assert _resolve_inferred_bank_code(extension="pdf", layout_inference_name=layout, bank_name=bank) == "033"
    if layout != "generic_statement_ptbr":
        assert resolve_conversion_model_label(layout_inference_name=layout, bank_name=bank) == layout


@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("footer,expected", [
    ("SAC Santander\n0800 000 0000\nOuvidoria Santander", "Santander"),
    ("Telefones de contato\nAtendimento por e-mail\napoioaempresa@bradesco.com.br\nSAC - Alô Bradesco", "Bradesco"),
    ("bb.com.br - Central de Atendimento BB", "Banco do Brasil"),
])
def test_institutional_contact_footer_overrides_layout_and_recipients(layout, footer, expected):
    assert resolve_bank_name(layout_inference_name=layout,
        extracted_text=f"EXTRATO DE CONTA\n{BODY}\n{footer}") == expected


@pytest.mark.parametrize("owner", ["Nome: Banco Santander", "Titular: BRADESCO", "Cliente: Banco Inter",
                                      "Nome:\nBanco Santander", "Extrato de conta - Titular: Banco Santander"])
def test_owner_bank_name_is_not_issuer_evidence(owner):
    text = f"EXTRATO DE CONTA\n{owner}\nAgência 0001 Conta 12345-6\n{BODY}"
    assert resolve_bank_name(layout_inference_name="account_detail_descending_cash_v1", extracted_text=text) is None
    assert resolve_bank_name(layout_inference_name="generic_statement_ptbr", extracted_text=text) is None


def test_title_and_holder_on_the_same_line_use_only_the_institutional_part():
    assert resolve_bank_name(extracted_text=f"Santander - Titular: Banco do Brasil\n{BODY}") == "Santander"


@pytest.mark.parametrize("counterparty", ["Banco Santander", "SAC Santander 0800 000 0000",
                                              "bb.com.br - Central de Atendimento BB"])
def test_contact_names_inside_a_transaction_do_not_identify_the_issuer(counterparty):
    text = f"EXTRATO DE CONTA\nData Histórico Valor\n03/10/2026 Pagamento {counterparty} -10,00"
    assert resolve_bank_name(layout_inference_name="account_detail_descending_cash_v1", extracted_text=text) is None
    assert resolve_bank_name(layout_inference_name="generic_statement_ptbr", extracted_text=text) is None


def test_unlisted_explicit_issuer_does_not_inherit_another_banks_code():
    layout = "caixa_app_extrato_ocr_sem_cabecalho_v1"
    bank = resolve_bank_name(layout_inference_name=layout,
        extracted_text=f"COOPERATIVA DE CREDITO VALE VERDE\nExtrato de conta corrente\n{BODY}")
    assert bank == "COOPERATIVA DE CREDITO VALE VERDE"
    assert _resolve_inferred_bank_code(extension="pdf", layout_inference_name=layout, bank_name=bank) is None


def test_conflicting_institutional_headers_do_not_silently_select_a_bank():
    layout = "caixa_app_extrato_ocr_sem_cabecalho_v1"
    bank = resolve_bank_name(layout_inference_name=layout,
        extracted_text=f"Banco Santander\nBanco Bradesco\nExtrato de conta corrente\n{BODY}")
    assert bank is None
    assert _resolve_inferred_bank_code(extension="pdf", layout_inference_name=layout, bank_name=bank) is None


def test_existing_layout_identity_remains_a_fallback_without_issuer_evidence():
    assert resolve_bank_name(layout_inference_name="itau_statement_ptbr", extracted_text=BODY) == "Itau"


@pytest.mark.parametrize("title,expected", [("Banco Santander", "Santander"),
    ("Banco Santander\nBanco Bradesco", "unknown")])
def test_capture_uses_the_same_issuer_priority_when_conversion_identity_is_unavailable(title, expected):
    from app.application.conversion.canonical_layout_capture import _bank_manifest

    manifest = _bank_manifest(bank_name=None, bank_code=None,
        layout_name="caixa_app_extrato_ocr_sem_cabecalho_v1", source_text=f"{title}\n{BODY}")
    assert manifest["name"] == expected
    assert manifest["code"] == ("033" if expected == "Santander" else None)


def issuer_pdf(*, footer=False, title="Banco Santander", owner=None):
    from tests.fixtures.recovery_acceptance import grid_pdf

    cells = [("EXTRATO DE CONTA", .65, .03), ("Agência: 0001 | Conta: 12345-6", .06, .06),
        ("Período: 01/10/2026 a 31/10/2026", .06, .09), ("Titular: EMPRESA FICTÍCIA", .06, .12),
        ("Saldo inicial", .08, .18), ("Entradas", .30, .18), ("Saídas", .52, .18), ("Saldo final", .74, .18),
        ("R$ 100,00", .08, .21), ("R$ 50,00", .30, .21), ("R$ 30,00", .52, .21), ("R$ 120,00", .74, .21),
        ("DETALHE DOS MOVIMENTOS", .06, .26), ("Data", .06, .30), ("Descrição", .20, .30),
        ("Valor", .72, .30), ("Saldo", .86, .30)]
    for date, y, description, amount, balance in [
        ("02/10/2026", .40, "Pagamento Caixa Economica Federal", "-R$ 10,00", "R$ 120,00"),
        ("01/10/2026", .48, "Pix recebido", "R$ 50,00", "R$ 130,00"),
        ("01/10/2026", .56, "Pix enviado Bradesco", "-R$ 20,00", "R$ 80,00"),
    ]:
        cells.extend([(date, .06, y), (description, .20, y), (amount, .71, y), (balance, .85, y)])
    if footer:
        cells.extend([("SAC Santander", .06, .90), ("0800 000 0000", .06, .92)])
    elif title:
        cells.insert(0, (title, .06, .03))
    if owner:
        cells.extend((line, .06, .14 + index * .015) for index, line in enumerate(owner.splitlines()))
    return grid_pdf([cells])


@pytest.mark.parametrize("footer", [False, True])
def test_native_reader_retains_structure_and_movements_with_explicit_issuer(footer, monkeypatch):
    from app.application.pdf_parser import parse_pdf_transactions

    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    result = parse_pdf_transactions(issuer_pdf(footer=footer))
    assert result.layout.layout_name == "account_detail_descending_cash_v1"
    assert [row.amount for row in result.transactions] == [-10, 50, -20]
    assert {row.bank_name for row in result.canonical_transactions} == {"Santander"}
    assert result.parse_metrics["balance_consistency_failed"] == 0
    assert all(row.source_regions for row in result.canonical_transactions)


@pytest.mark.parametrize("source", ["header", "conflicting_header"])
def test_stored_unknown_issuer_never_regains_the_layout_bank_on_export(tmp_path, source):
    from app.application.storage_service import TempAnalysisStorage
    from tests.test_export_artifact_service import _build_snapshot

    snapshot = _build_snapshot()
    snapshot.layout_inference_name = "caixa_app_extrato_ocr_sem_cabecalho_v1"
    snapshot.bank_name = "BANCO FICTICIO" if source == "header" else None
    snapshot.bank_code = None
    snapshot.pdf_processing_metrics = {"statement_issuer_source": source}
    storage = TempAnalysisStorage(root_dir=tmp_path)
    storage.save_analysis(snapshot)
    storage.apply_convert_edits(snapshot.analysis_id, [{"row_id": "row_1", "date": "2026-04-01",
        "description": "SALARIO CORRIGIDO", "credit": 100}])
    for overrides in ({}, {"bank_branch": "1234"}, {"bank_code": "033"}):
        path = storage.get_convert_report_path(snapshot.analysis_id, file_format="ofx", **overrides)
        assert f"<BANKID>{overrides.get('bank_code', '000')}" in path.read_text(encoding="utf-8")
