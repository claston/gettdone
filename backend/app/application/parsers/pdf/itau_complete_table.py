"""Itaú complete web table: account cash controls differ from automatic funds."""
import re
from dataclasses import replace
from io import BytesIO

from pypdf.generic import ContentStream

from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.reader import open_pdf_reader
from app.application.parsers.pdf.statement_review import financial_mismatch
from app.application.parsers.pdf.text_extraction import validate_native_pdf_fonts
from app.application.parsers.pdf.verified_statements import DATE, NUMBER, VerifiedStatement, _row, day, fail, money

_CONTROLS = {"SALDO ANTERIOR", "SALDO MOVIMENTACAO CONTA", "SALDO TOTAL DISPONIVEL DIA", "SALDO APLIC. AUT."}
_MONEY = re.compile(rf"[+-]?{NUMBER}")


def parse_itau_complete_table(raw, native_pages):
    normalized = upper("\n".join(native_pages))
    if not all(marker in normalized for marker in ("LANCAMENTOS DO PERIODO:", "RAZAO SOCIAL", "CNPJ/CPF", "SALDO MOVIMENTACAO CONTA")):
        return None
    reader = open_pdf_reader(BytesIO(raw))
    pages = []
    for page in reader.pages:
        validate_native_pdf_fonts(page)
        content = (page.extract_text(extraction_mode="layout") or "").strip()
        if not content:
            # Browser exports can append a clipping path without painting anything.
            # An image, text-show operator or annotation must never be skipped.
            allowed = {b"q", b"Q", b"cm", b"w", b"J", b"j", b"M", b"d", b"m", b"l", b"c", b"v", b"y", b"h", b"re", b"W", b"W*", b"n"}
            stream = ContentStream(page.get_contents(), reader)
            if page.get("/Annots") or any(operator not in allowed for _, operator in stream.operations):
                fail("Uma página do extrato Itaú contém dados sem texto legível.")
        pages.append(content)
    periods = set(re.findall(rf"LANCAMENTOS DO PERIODO:\s*({DATE})\s+ATE\s+({DATE})", upper("\n".join(pages))))
    if len(periods) != 1:
        fail()
    first, last = next(iter(periods))
    start, end = day(first), day(last)
    if start > end:
        fail()
    records = []
    finished = False
    for number, page in enumerate(pages, 1):
        if finished and page.strip():
            fail("Há outra página após o encerramento do extrato Itaú.")
        columns = None
        for line_number, line in enumerate(page.splitlines(), 1):
            label = upper(line.strip())
            if not label:
                continue
            if label == "SALDO DA CONTA CORRENTE":
                finished = True
            if finished:
                continue
            if re.fullmatch(r"DATA\s+LANCAMENTOS\s+RAZAO SOCIAL\s+CNPJ/CPF\s+VALOR \(R\$\)\s+SALDO \(R\$\)", label):
                columns = [re.search(key, line, re.I).start() for key in
                           (r"Lan[çc]amentos", r"Raz[ãa]o Social", r"CNPJ/CPF", r"Valor", r"Saldo")]
                continue
            if columns is None:
                continue
            match = re.match(rf"^\s*({DATE})\s+(.+)$", line)
            if match:
                amounts = list(_MONEY.finditer(line))
                if len(amounts) != 1 or line[amounts[0].end():].strip():
                    fail("Uma linha Itaú está sem valor ou saldo legível.")
                token = amounts[0]
                prefix = " ".join(line[match.end(1):token.start()].split())
                control = upper(prefix) in _CONTROLS
                boundary = (columns[3] + columns[4]) / 2
                if (token.start() >= boundary) != control:
                    fail("Um valor ou saldo Itaú está na coluna incorreta.")
                fields = [line[columns[0]-2:columns[1]-2].strip(),
                          line[columns[1]-2:columns[2]-2].strip(), line[columns[2]-2:token.start()].strip()]
                records.append({"date": day(match[1]), "label": upper(prefix), "fields": fields,
                    "control": control, "value": money(token[0]), "page": number, "line": line_number,
                    "refs": [(number, line_number)]})
            else:
                if re.match(r"^\s*\d{2}/", line) or _MONEY.search(line):
                    fail("Há uma linha financeira Itaú sem data ou valor associado.")
                if not records or records[-1]["control"] or line[:columns[0]-2].strip() or line[columns[3]-3:].strip():
                    fail("Uma continuação de histórico Itaú não pode ser associada.")
                record = records[-1]
                for index, (low, high) in enumerate(zip([columns[0]-2, columns[1]-2, columns[2]-2],
                                                     [columns[1]-2, columns[2]-2, columns[3]-3])):
                    record["fields"][index] += " " + line[low:high].strip()
                record["refs"].append((number, line_number))
    if not finished or not records or any(a["date"] < b["date"] for a, b in zip(records, records[1:])):
        fail("A tabela completa Itaú não pôde ser delimitada ou ordenada.")
    openings = [r for r in records if r["label"] == "SALDO ANTERIOR"]
    if len(openings) != 1 or records[-1] is not openings[0] or openings[0]["date"] >= start:
        fail("Falta o saldo anterior inequívoco do Itaú.")
    running = opening = openings[0]["value"]
    controls, transaction_days, checks = {}, set(), 0
    for record in reversed(records[:-1]):
        date = record["date"]
        if not start <= date <= end:
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
        r["value"], None), source_line_refs=tuple(r["refs"])) for r in records if not r["control"]]
    return VerifiedStatement(rows, "itau_empresas_extrato_completo_tabela_v1", opening, closing,
        printed_balance_checks=checks, candidate_rows=len(rows), balance_checkpoints={}), pages
