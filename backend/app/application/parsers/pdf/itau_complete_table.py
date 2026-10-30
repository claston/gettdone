"""Itaú complete web table: account cash controls differ from automatic funds."""
import re
from dataclasses import replace
from io import BytesIO

from pypdf.generic import ContentStream

from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.document_period import period_contains, unique_document_period
from app.application.parsers.pdf.native_table_source import read_native_table_source
from app.application.parsers.pdf.reader import open_pdf_reader
from app.application.parsers.pdf.statement_review import financial_mismatch
from app.application.parsers.pdf.text_extraction import validate_native_pdf_fonts
from app.application.parsers.pdf.verified_statements import DATE, VerifiedStatement, _row, fail
from app.application.pdf_document_segmentation import segment_pdf_pages


def parse_itau_complete_table(raw, native_pages):
    normalized = upper("\n".join(native_pages))
    if not all(marker in normalized for marker in ("LANCAMENTOS DO PERIODO:", "RAZAO SOCIAL", "CNPJ/CPF", "SALDO MOVIMENTACAO CONTA")):
        return None
    segment_pdf_pages(native_pages)
    reader = open_pdf_reader(BytesIO(raw))
    pages = []
    for page in reader.pages:
        validate_native_pdf_fonts(page)
        content = (page.extract_text(extraction_mode="layout") or "").rstrip()
        if not content.strip():
            # Browser exports can append a clipping path without painting anything.
            # An image, text-show operator or annotation must never be skipped.
            allowed = {b"q", b"Q", b"cm", b"w", b"J", b"j", b"M", b"d", b"m", b"l", b"c", b"v", b"y", b"h", b"re", b"W", b"W*", b"n"}
            stream = ContentStream(page.get_contents(), reader)
            if page.get("/Annots") or any(operator not in allowed for _, operator in stream.operations):
                fail("Uma página do extrato Itaú contém dados sem texto legível.")
        pages.append(content)
    from app.application.parsers.pdf.itau_complete_rows import read_itau_complete_rows

    source = read_native_table_source(raw)
    pages = ["\n".join(line.text for line in page.lines) for page in source.pages]
    periods = set(re.findall(rf"LANCAMENTOS DO PERIODO:\s*({DATE})\s+ATE\s+({DATE})", normalized))
    start, end = unique_document_period(periods)
    records = read_itau_complete_rows(raw, source)
    openings = [r for r in records if r["label"] == "SALDO ANTERIOR"]
    if len(openings) != 1 or start is not None and openings[0]["date"] >= start:
        fail("Falta o saldo anterior inequívoco do Itaú.")
    # Keep physical order for exports, but validate in accounting order. The
    # opening's position disambiguates within-day order without sorting rows.
    if records[0] is openings[0]:
        ordered = records
    elif records[-1] is openings[0]:
        ordered = list(reversed(records))
    else:
        fail("O saldo anterior está no meio dos movimentos Itaú.")
    if any(a["date"] > b["date"] for a, b in zip(ordered, ordered[1:])):
        fail("A ordem de datas da tabela Itaú não pode ser confirmada.")
    running = opening = openings[0]["value"]
    controls, transaction_days, checks = {}, set(), 0
    for record in ordered[1:]:
        date = record["date"]
        if not period_contains(date, start, end):
            financial_mismatch()
        if not record["control"]:
            running += record["value"]
            transaction_days.add(date)
        else:
            daily = controls.setdefault(date, {})
            if record["label"] in daily:
                fail("Há saldos Itaú repetidos para a mesma data e categoria.")
            daily[record["label"]] = record["value"]
            if record["label"] == "SALDO MOVIMENTACAO CONTA":
                checks += 1
                if running != record["value"]:
                    financial_mismatch()
    if not transaction_days or any("SALDO MOVIMENTACAO CONTA" not in controls.get(date, {}) for date in transaction_days):
        fail("Um dia de movimentação Itaú está sem controle da conta.")
    for daily in controls.values():
        if all(key in daily for key in ("SALDO MOVIMENTACAO CONTA", "SALDO APLIC. AUT.", "SALDO TOTAL DISPONIVEL DIA")):
            checks += 1
            if daily["SALDO MOVIMENTACAO CONTA"] + daily["SALDO APLIC. AUT."] != daily["SALDO TOTAL DISPONIVEL DIA"]:
                financial_mismatch()
    closing = controls[max(transaction_days)]["SALDO MOVIMENTACAO CONTA"]
    rows = [replace(_row(r["page"], r["line"], r["date"], " ".join(" ".join(f.split()) for f in r["fields"]).strip(),
        r["value"], None), source_line_refs=tuple(r["refs"]), source_regions=r["regions"]) for r in records if not r["control"]]
    return VerifiedStatement(rows, "itau_empresas_extrato_completo_tabela_v1", opening, closing,
        printed_balance_checks=checks, candidate_rows=len(rows), balance_checkpoints={}), pages
