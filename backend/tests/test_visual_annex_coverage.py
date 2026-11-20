"""Fictional OCR pages: metadata must not become missing bank movements."""
from dataclasses import replace

import pytest

from app.application.document_extraction_models import ExtractedLine, ExtractedPage, RawDocumentExtraction
from app.application.errors import OcrFinancialAmbiguityError
from app.application.pdf_parser import _original_visual_provenance
from tests.test_mixed_batch_failure_feedback import uncovered_result


def page(cells):
    return ExtractedPage(2, [ExtractedLine(
        str(i), 2, i, text, bbox={"left": x, "top": y, "width": .15, "height": .008}
    ) for i, (text, x, y) in enumerate(cells)])


def fiscal_page():
    return page([
        ("DANFSe DOCUMENTO AUXILIAR DA NFS-e", .1, .03),
        ("Competência da NFS-e", .25, .09), ("Data e Hora da emissão da NFS-e", .5, .09),
        ("15/06/2026", .25, .105), ("07/07/2026 16:51:04", .5, .105),
        ("Data e Hora da emissão da DPS", .5, .12), ("07/07/2026 16:51:04", .5, .135),
        ("VALOR DOS SERVIÇOS", .1, .6), ("100,00", .1, .62),
        ("VALOR LÍQUIDO", .5, .6), ("100,00", .5, .62),
    ])


def automatic_details():
    return page([
        ("Saldo final", .35, .1), ("0,00", .85, .1),
        ("totalizador de aplicações automáticas", .35, .13),
        ("Conta Corrente Aplicações Automáticas", .1, .2),
        ("Aplic Aut Mais (CDB)", .1, .22),
        ("saldo em", .4, .25), ("05/26 R$", .4, .265),
        ("saldo em", .8, .25), ("06/26 R$", .8, .265),
        ("movimentação aplicações/resgates antecipados e vencimentos", .1, .3),
        ("rendimento", .5, .32), ("saldo de principal", .8, .32),
        ("aplicado R$", .8, .34), ("data", .1, .34),
        ("01/06", .1, .36), ("100,00", .3, .36), ("0,01", .5, .36), ("150,00", .8, .36),
        ("02/06", .1, .38), ("20,00", .3, .38), ("0,02", .5, .38), ("130,00", .8, .38),
        ("LOGO", .02, .38),
    ])


def result_with(source):
    result = uncovered_result()
    return replace(result, source_page_texts=(result.source_page_texts[0], "\n".join(line.text for line in source.lines)),
                   source_extraction=RawDocumentExtraction("aws_textract", "visual", [source], {}))


@pytest.mark.parametrize("factory", [fiscal_page, automatic_details])
def test_labelled_metadata_and_closed_automatic_details_do_not_reject_statement(factory):
    result = result_with(factory())
    checked = _original_visual_provenance(result, b"original", b"visual")
    assert checked.transactions == result.transactions
    assert checked.source_page_texts == result.source_page_texts
    assert checked.parse_metrics["export_recommendation"] == "review_recommended"


@pytest.mark.parametrize("factory", [fiscal_page, automatic_details])
@pytest.mark.parametrize("position", ["before", "after"])
def test_metadata_does_not_hide_bank_continuation_on_same_page(factory, position):
    source = factory()
    top = .01 if position == "before" else .8
    movements = page([
        ("03/06/2026 PIX 20,00", .1, top), ("04/06/2026 PIX 30,00", .1, top + .015),
    ]).lines
    source = replace(source, lines=sorted([*source.lines, *movements], key=lambda line: line.bbox["top"]))
    with pytest.raises(OcrFinancialAmbiguityError):
        _original_visual_provenance(result_with(source), b"original", b"visual")


def test_automatic_details_without_printed_ledger_closure_remain_uncovered():
    source = automatic_details()
    source = replace(source, lines=[line for line in source.lines if line.bbox["top"] >= .2])
    with pytest.raises(OcrFinancialAmbiguityError):
        _original_visual_provenance(result_with(source), b"original", b"visual")


def test_metadata_without_geometry_is_not_assumed_to_cover_dates():
    source = fiscal_page()
    source = replace(source, lines=[replace(line, bbox=None) for line in source.lines])
    with pytest.raises(OcrFinancialAmbiguityError):
        _original_visual_provenance(result_with(source), b"original", b"visual")


def test_fiscal_labels_cannot_exempt_dates_in_another_column():
    source = fiscal_page()
    source = replace(source, lines=[replace(line, bbox={**line.bbox, "left": .05})
                                   if line.text.startswith(("15/06", "07/07")) else line for line in source.lines])
    with pytest.raises(OcrFinancialAmbiguityError):
        _original_visual_provenance(result_with(source), b"original", b"visual")


def test_automatic_table_does_not_exempt_free_form_descriptions():
    source = automatic_details()
    source = replace(source, lines=[*source.lines, *page([
        ("MOVIMENTO NÃO IDENTIFICADO", .2, .36), ("OUTRO MOVIMENTO", .2, .38),
    ]).lines])
    with pytest.raises(OcrFinancialAmbiguityError):
        _original_visual_provenance(result_with(source), b"original", b"visual")
