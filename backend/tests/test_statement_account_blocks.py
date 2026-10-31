"""Fictional consolidated statements keep each owner's account and cash controls."""

from dataclasses import replace

import pytest

from app.application import pdf_parser
from app.application.default_conversion_pipeline import build_default_conversion_pipeline
from app.application.errors import InvalidFileContentError
from tests.fixtures.positioned_cash_statements import grid, positioned_pdf
from tests.test_recovery_sweep_ocr import extraction


def account_page(*, account="12345-6", branch="0017", month="setembro", number="09", opening=100,
                 missing_account=False, unreadable=False, mismatch=False, owner="EMPRESA FICTICIA LTDA"):
    previous = "31/08" if number == "09" else "30/09"
    final = "30/09" if number == "09" else "31/10"
    cells = [
        (25, 785, f"Santander EXTRATO CONSOLIDADO INTELIGENTE {month}/2026"),
        (25, 770, "Extrato_PJ_A4_Inteligente"), (25, 750, f"Resumo - {month}/2026"),
        (25, 735, "Nome"), (25, 723, owner),
        (25, 708, "Agencia"), (100, 708, "Conta Corrente"),
        (25, 696, branch), (100, 696, "ilegivel" if missing_account else account),
        (25, 675, "(=) Saldo de Conta Corrente em " + previous), (400, 675, f"{opening},00"),
        (25, 655, "(+) Total de Creditos"), (400, 655, "50,00"),
        (25, 635, "(-) Total de Debitos"), (400, 635, "20,00"),
        (25, 615, "(=) Saldo de Conta Corrente em " + final), (400, 615, f"{opening+30},00"),
        (25, 585, "Conta Corrente Movimentacao"), (345, 560, "Movimentos (R$)"), *grid(560, santander=True),
        (80, 530, "SALDO EM " + previous), (540, 530, f"{opening},00"),
        (25, 500, f"01/{number}"), (80, 500, "PIX RECEBIDO"), (270, 500, "1001"),
        (365, 500, "50,00"), (540, 500, f"{opening+50},00"),
        (25, 470, f"02/{number}"), (80, 470, "PIX ENVIADO"), (270, 470, "1002"),
        (445, 470, "2?,00-" if unreadable else "20,00-"),
        (540, 470, f"{opening+30+int(mismatch)},00"),
        (80, 440, "SALDO EM " + final), (540, 440, f"{opening+30},00"),
        (25, 410, "Saldos por Periodo"),
    ]
    return cells


def consolidated_pages(**kwargs):
    first = account_page()
    second = account_page(account="98765-4", branch="0039", opening=200, **kwargs)
    annex = [(25, 780, "Santander EXTRATO CONSOLIDADO INTELIGENTE setembro/2026"),
             (25, 760, "Continuacao 1"), (25, 740, "Nome EMPRESA FICTICIA LTDA"),
             (25, 720, "Agencia Conta Corrente"), (25, 705, "0017 12345-6"),
             (25, 670, "Investimentos ContaMax Empresarial"), (25, 640, "TOTAL GERAL 7.000,00")]
    return [[(25, 780, "Santander EXTRATO CONSOLIDADO INTELIGENTE setembro/2026"),
             (25, 760, "Extrato_PJ_A4_Inteligente")], first, annex, second]


def inactive_page():
    return [(x, y, "0,00" if text in {"50,00", "20,00"} else "200,00" if text == "230,00" else text)
            for x, y, text in account_page(account="98765-4", branch="0039", opening=200) if y not in {500, 470}]


def ocr_pages(pages):
    source = extraction([])
    result = []
    for i, cells in enumerate(pages, 1):
        page = extraction([(text, x/600, (800-y)/800) for x, y, text in cells], page=i).pages[0]
        result.append(replace(page, lines=[replace(line, id=f"p{i}-{line.id}") for line in page.lines]))
    return replace(source, pages=result)


def parse_pages(pages, representation):
    if representation == "native":
        return pdf_parser.parse_pdf_transactions(positioned_pdf(pages))
    return pdf_parser._parse_scanned_pdf_with_textract_text_pages(ocr_pages(pages))


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("PDF_MULTI_ACCOUNT_ENABLED", "true")
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")


@pytest.mark.parametrize("representation", ["native", "ocr"])
def test_consolidated_accounts_have_independent_rows_balances_and_physical_pages(representation):
    result = parse_pages(consolidated_pages(), representation)
    assert [t.amount for t in result.transactions] == [50, -20, 50, -20]
    rows = result.canonical_transactions
    assert [r.statement_account["account_number"] for r in rows] == ["12345-6"] * 2 + ["98765-4"] * 2
    assert [r.statement_account["bank_branch"] for r in rows] == ["0017"] * 2 + ["0039"] * 2
    assert [r.source_page for r in rows] == [2, 2, 4, 4]
    assert all(r.source_regions and r.source_line_refs for r in rows)
    parts = [p for p in result.document_parts if p["included_in_statement"]]
    assert [(p["opening_balance"], p["closing_balance"]) for p in parts] == [(100, 130), (200, 230)]
    assert result.parse_metrics["statement_accounts_count"] == 2
    assert result.parse_metrics["balance_bounds_unavailable"] == 1
    assert "verified_closing_balance" not in result.parse_metrics
    assert result.parse_metrics["balance_consistency_failed"] == 0


@pytest.mark.parametrize("representation", ["native", "ocr"])
@pytest.mark.parametrize("kwargs", [{"missing_account": True}, {"unreadable": True}])
def test_a_later_unassociated_or_unreadable_account_is_never_silently_omitted(representation, kwargs):
    with pytest.raises(InvalidFileContentError):
        parse_pages(consolidated_pages(**kwargs), representation)


@pytest.mark.parametrize("representation", ["native", "ocr"])
def test_counterparty_details_and_investments_do_not_start_an_account(representation):
    pages = consolidated_pages()
    pages[1] += [(80, 458, "FAV. Agencia 9999 Conta 11111-1")]
    result = parse_pages(pages, representation)
    assert result.parse_metrics["statement_accounts_count"] == 2
    assert len(result.transactions) == 4


@pytest.mark.parametrize("representation", ["native", "ocr"])
def test_account_balance_mismatch_preserves_cash_and_requires_review(representation):
    result = parse_pages(consolidated_pages(mismatch=True), representation)
    assert [r.amount for r in result.transactions] == [50, -20, 50, -20]
    assert result.parse_metrics["export_recommendation"] == "review_recommended"
    assert any(p["requires_review"] for p in result.document_parts)


@pytest.mark.parametrize("representation", ["native", "ocr"])
def test_continuity_is_checked_within_each_account_across_months(representation):
    pages = [account_page(), account_page(account="98765-4", branch="0039", opening=200),
             account_page(month="outubro", number="10", opening=130),
             account_page(account="98765-4", branch="0039", month="outubro", number="10", opening=231)]
    result = parse_pages(pages, representation)
    assert len(result.transactions) == 8
    assert result.parse_metrics["statement_accounts_count"] == 2
    assert result.parse_metrics["statement_blocks_count"] == 4
    assert result.parse_metrics["export_recommendation"] == "review_recommended"
    assert "statement_account_blocks_not_contiguous" in result.parse_metrics["canonical_warning_types"]


def test_pipeline_does_not_assign_one_global_account_or_cross_account_duplicate_groups():
    raw = positioned_pdf(consolidated_pages())
    data = build_default_conversion_pipeline().run(filename="consolidado.pdf", raw_bytes=raw, analysis_id="an_accounts").analysis_data
    assert data.opening_balance is data.closing_balance is None
    assert data.bank_branch is data.account_number is None
    assert [r.statement_account["account_number"] for r in data.report_transactions] == ["12345-6"] * 2 + ["98765-4"] * 2
    assert data.potential_duplicates == 0


def test_other_layouts_keep_the_multiple_account_safety_guard():
    from tests.test_multi_period_statements import bb_month, pdf

    with pytest.raises(InvalidFileContentError):
        pdf_parser.parse_pdf_transactions(pdf(bb_month(1) + bb_month(2, account="98765-4")))


def test_rollout_switch_keeps_the_previous_behavior_until_all_services_are_ready(monkeypatch):
    monkeypatch.delenv("PDF_MULTI_ACCOUNT_ENABLED")
    with pytest.raises(InvalidFileContentError):
        parse_pages(consolidated_pages(), "native")


@pytest.mark.parametrize("representation", ["native", "ocr"])
def test_an_inactive_second_account_is_preserved_without_invented_transactions(representation):
    result = parse_pages([account_page(), inactive_page()], representation)
    assert [t.amount for t in result.transactions] == [50, -20]
    assert len(result.document_parts) == 2
    assert result.document_parts[1]["closing_balance"] == 200


@pytest.mark.parametrize("representation", ["native", "ocr"])
def test_different_holders_are_not_silently_accepted_as_one_consolidated_statement(representation):
    with pytest.raises(InvalidFileContentError):
        parse_pages(consolidated_pages(owner="OUTRA EMPRESA LTDA"), representation)


def test_unidentified_leading_movements_are_not_assigned_to_the_next_account():
    pages = consolidated_pages()
    pages[0] += [(25, 700, "01/09/2026 PIX RECEBIDO 999,00")]
    with pytest.raises(InvalidFileContentError):
        parse_pages(pages, "native")
