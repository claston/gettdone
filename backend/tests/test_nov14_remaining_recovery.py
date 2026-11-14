"""Fictional regressions for the remaining November 14 recovery failures."""
import pytest

from app.application import pdf_parser
from app.application.errors import PdfFinancialValidationError
from tests.fixtures.recovery_acceptance import grid_pdf
from tests.test_final_recovery_native import english_statement, parse
from tests.test_oct20_followup_ocr import ocr


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")


def combined_pages():
    from tests.test_bb_combined_descriptions import pages

    result = [[("0000" if text == "1234-5" else text, x, y) for text, x, y in page] for page in pages()]
    result[0] += [("31/10/2025", .08, .15), ("1234-5", .17, .15),
                  ("Saldo Anterior", .25, .15), ("10,00 D", .87, .15)]
    return result


@pytest.mark.parametrize("mode", ["native", "ocr"])
def test_bb_origin_without_check_digit_and_opening_position_are_not_cash(mode):
    pages = combined_pages()
    result = (pdf_parser.parse_pdf_transactions(grid_pdf(pages)) if mode == "native"
              else pdf_parser._parse_textract_text_pages(ocr(pages)))
    rows = {row.external_reference_id: row for row in result.canonical_transactions}
    assert {key: row.amount for key, row in rows.items()} == {"001103": -20, "001104": -20, "001105": 50}
    assert result.parse_metrics["balance_validation_status"] == "combined_cash_investment_not_comparable"
    assert result.parse_metrics["financial_controls_validated"] == 0
    assert all("Saldo" not in row.description for row in result.transactions)
    assert {page for page, _ in rows["001104"].source_line_refs} == {1, 2}


def test_bb_checks_preserve_both_printed_signs_and_negative_current_balance():
    text = english_statement().replace(" DEP ", " CHECK ").replace(" DEBIT ", " CHECK ")
    result = parse(text.replace("Current Balance R$0.00", "Current Balance R$-5.00"))
    assert [row.amount for row in result.transactions] == [1200, -1200]
    assert result.parse_metrics["verified_closing_balance"] == -5
    assert result.parse_metrics["export_recommendation"] == "review_recommended"


@pytest.mark.parametrize("old,new", [(" DEP ", " UNKNOWN "), (" DEP ", " DEBIT "),
                                    (" DEBIT ", " DEP "), ("Current Balance R$0.00", "Current Balance R$--5.00")])
def test_bb_english_unknown_type_or_conflicting_sign_remains_rejected(old, new):
    with pytest.raises(PdfFinancialValidationError):
        parse(english_statement().replace(old, new))


def credisis_pages(*, reference=None, closing="80,00"):
    cells = [("Ouvidoria CREDISIS", .05, .03),
             ("Data", .1, .1), ("Código", .21, .1), ("Descrição", .3, .1),
             ("Valor", .7, .1), ("Saldo", .83, .1),
             ("Saldo final", .3, .15), (f"R$ {closing}", .83, .15),
             ("02/09/2026", .1, .25), ("SAQUE C/C NO CAIXA-", .3, .25),
             ("R$ -20,00", .7, .25), ("R$ 80,00", .83, .25),
             ("Saldo anterior", .3, .35), ("R$ 100,00", .83, .35),
             ("Saldos em 30/09/2026", .1, .45)]
    if reference:
        cells.append((reference, .21, .25))
    return [cells]


@pytest.mark.parametrize("reference", [None, "REF001"])
def test_credisis_optional_reference_preserves_printed_cash_and_provenance(reference):
    result = pdf_parser.parse_pdf_transactions(grid_pdf(credisis_pages(reference=reference)))
    assert [row.amount for row in result.transactions] == [-20]
    assert result.canonical_transactions[0].external_reference_id == reference
    assert result.canonical_transactions[0].source_regions
    assert result.parse_metrics["verified_opening_balance"] == 100
    assert result.parse_metrics["verified_closing_balance"] == 80
    assert not result.parse_metrics.get("statement_validation_failed")


def test_credisis_missing_reference_does_not_hide_a_real_balance_difference():
    result = pdf_parser.parse_pdf_transactions(grid_pdf(credisis_pages(closing="81,00")))
    assert [row.amount for row in result.transactions] == [-20]
    assert result.parse_metrics["statement_validation_failed"] == 1


@pytest.mark.parametrize("mode", ["native", "ocr"])
def test_sisbr_blocked_opening_spacing_and_cash_summary_synonym(mode, monkeypatch):
    from tests import test_oct27_final_priority as fixture

    monkeypatch.setattr(fixture, "grid_pdf", lambda pages: pages)
    original = fixture.sicoob_split_signs()
    if mode == "ocr":
        # The separate-sign native fixture places its amount .006 above the
        # date; use aligned OCR cells so this test isolates the control labels.
        original = [[("200,00D" if text == "200,00" else text, left,
                      .47 if text == "200,00" else top) for text, left, top in page if text != "D"]
                    for page in original]
    pages = [[(text.replace("SALDO BLOQ.ANTERIOR", "SALDO BLOQ. ANTERIOR")
               .replace("(+) SALDO EM CONTA:", "SALDO EM C.CORRENTE(+):"),
               left, top) for text, left, top in page] for page in original]
    result = (pdf_parser.parse_pdf_transactions(grid_pdf(pages)) if mode == "native"
              else pdf_parser._parse_scanned_pdf_with_textract_text_pages(ocr(pages)))
    assert [row.amount for row in result.transactions] == [50, -200]
    assert result.parse_metrics["verified_opening_balance"] == 100
    assert result.parse_metrics["verified_closing_balance"] == -50
    assert not result.parse_metrics.get("statement_validation_failed")


def nubank_inactive_pages(*, mutation=None):
    cells = [("CLIENTE FICTÍCIO", .10, .04), ("Agência 0001 Conta 123456-7", .55, .08),
             ("01 DE OUTUBRO DE 2026 a 31 DE OUTUBRO DE 2026", .10, .15),
             ("VALORES EM R$", .79, .15), ("Saldo inicial", .53, .19), ("6,92", .87, .19),
             ("Rendimento líquido", .53, .22), ("+0,00", .87, .22),
             ("Saldo final do período", .10, .225), ("R$ 6,92", .10, .245),
             ("Total de entradas", .53, .25), ("+0,00", .87, .25),
             ("Total de saídas", .53, .28), ("-0,00", .87, .28),
             ("Saldo final do período", .53, .31), ("6,92", .87, .31),
             ("Nenhuma movimentação realizada.", .10, .36),
             ("O saldo líquido corresponde ao total de depósitos e rendimentos em conta.", .10, .42),
             ("Nu Pagamentos S.A. - Instituição de Pagamento", .10, .50),
             ("nubank.com.br/contatos#ouvidoria", .10, .60), ("1 de 1", .87, .70)]
    if mutation == "nonzero_total":
        cells = [("+1,00" if text == "+0,00" and top == .25 else text, left, top) for text, left, top in cells]
    elif mutation == "different_balance":
        cells = [("7,92" if text == "6,92" and top == .31 else text, left, top) for text, left, top in cells]
    elif mutation == "movement":
        cells.append(("05/10/2026 PIX ENVIADO -10,00", .10, .39))
    elif mutation == "extra_page":
        return [cells, [("05/10/2026 PIX ENVIADO -10,00", .10, .39)]]
    return [cells]


@pytest.mark.parametrize("mode", ["native", "ocr"])
def test_nubank_explicit_inactivity_retains_nonzero_equal_balances_and_period(mode):
    pages = nubank_inactive_pages()
    result = (pdf_parser.parse_pdf_transactions(grid_pdf(pages)) if mode == "native"
              else pdf_parser._parse_scanned_pdf_with_textract_text_pages(ocr(pages)))
    assert not result.transactions
    assert result.parse_metrics["statement_no_activity"] == 1
    assert result.parse_metrics["verified_opening_balance"] == 6.92
    assert result.parse_metrics["verified_closing_balance"] == 6.92
    assert result.parse_metrics["statement_period_start"] == "2026-10-01"
    assert result.parse_metrics["statement_period_end"] == "2026-10-31"


@pytest.mark.parametrize("mutation", ["nonzero_total", "different_balance", "movement", "extra_page"])
def test_nubank_inactivity_declaration_cannot_hide_other_financial_evidence(mutation):
    from app.application.parsers.pdf.explicit_inactive import read_native_inactive, recognize_explicit_inactive

    pages = nubank_inactive_pages(mutation=mutation)
    texts = ["\n".join(text for text, _, _ in page) for page in pages]
    assert recognize_explicit_inactive(texts) is None
    assert read_native_inactive(grid_pdf(pages), texts) is None


@pytest.mark.parametrize("merged", [False, True])
def test_sisbr_ocr_long_right_aligned_amount_and_merged_history(merged):
    from dataclasses import replace

    from app.application.document_extraction_models import ExtractedWord

    source = ocr([[
        ("SICOOB PLATAFORMA DE SERVIÇOS FINANCEIROS DO SICOOB - SISBR", .10, .02),
        ("COOP.: 1234-5 HISTÓRICO DE MOVIMENTAÇÃO", .10, .04),
        ("CONTA: 12345-6", .10, .06), ("PERÍODO: 01/10/2026 - 31/10/2026", .10, .10),
        ("DATA", .10, .15), ("HISTÓRICO", .23, .15), ("VALOR", .77, .15),
        ("30/09", .10, .20), ("SALDO ANTERIOR", .23, .20), ("0,00C", .78, .20),
        ("01/10", .10, .30), ("PIX RECEBIDO", .23, .30), ("250.000,00C", .72, .30),
        ("DOC.: Pix", .23, .34), ("01/10", .10, .40), ("SALDO DO DIA", .23, .40), ("250.000,00C", .72, .40),
        ("RESUMO", .40, .50), ("(+) SALDO EM CONTA:", .10, .55), ("250.000,00C", .72, .55),
    ]])
    page = source.pages[0]
    lines = [replace(line, bbox={**line.bbox, "width": .11}) if line.text == "250.000,00C" else line for line in page.lines]
    words = []
    if merged:
        history = next(line for line in lines if line.text == "PIX RECEBIDO")
        amount = next(line for line in lines if line.text == "250.000,00C" and line.bbox["top"] == .30)
        words = [ExtractedWord("history", 1, 1, history.text, bbox=history.bbox),
                 ExtractedWord("amount", 1, 2, amount.text, bbox=amount.bbox)]
        lines = [replace(line, text=history.text + " " + amount.text,
                         bbox={**history.bbox, "width": .60}, word_ids=("history", "amount")) if line.id == history.id else line
                 for line in lines if line.id != amount.id]
    source = replace(source, pages=[replace(page, lines=lines, words=words)])
    result = pdf_parser._parse_scanned_pdf_with_textract_text_pages(source)
    assert [row.amount for row in result.transactions] == [250000]
    assert result.transactions[0].description == "PIX RECEBIDO"
    assert result.parse_metrics["verified_closing_balance"] == 250000
    assert result.canonical_transactions[0].source_regions


@pytest.mark.real_http
def test_recovered_pdf_and_supported_csv_through_public_http(tmp_path):
    from tests.ocr_conversion_http_helpers import public_client

    with public_client(tmp_path) as client:
        owner = {"anonymous_fingerprint": "fp-nov14-remaining"}
        for filename, content, mime in [
            ("fictional.pdf", grid_pdf(combined_pages()), "application/pdf"),
            ("fictional.csv", b"date,description,amount\n2026-10-01,TESTE,10\n", "text/csv"),
        ]:
            response = client.post("/convert", data=owner, files={"file": (filename, content, mime)})
            assert response.status_code == 200, response.text
            identifier = response.json()["processing_id"]
            assert client.get(f"/report/{identifier}", params=owner).status_code == 200
            for format in ("csv", "xlsx", "ofx"):
                exported = client.get(f"/convert-report/{identifier}", params={**owner, "format": format})
                assert exported.status_code == 200, exported.text
        assert client.post("/analyze").status_code == 405  # Endpoint disabled in the current public API.
        assert client.get("/report/nonexistent-nov14-remaining", params=owner).status_code == 404
