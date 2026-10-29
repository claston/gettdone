"""Monthly cash tables with explicit amount columns and printed date context."""

import re
from dataclasses import replace

from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.followup_ocr import _headers, _source
from app.application.parsers.pdf.row_geometry import nearest_row
from app.application.parsers.pdf.statement_review import financial_mismatch
from app.application.parsers.pdf.verified_statements import DATE, NUMBER, VerifiedStatement, _row, day, fail, money

_SIGNED = re.compile(rf"([+-]?)({NUMBER})([+-]?)")
_LOT = re.compile(r"^(\d{6,8})\s+(\d{3}#?)\s+(\d{2}/\d{2}/(?:\d{4}|\d{2}))\s+(.+)$")
_MONTHS = "JANEIRO FEVEREIRO MARCO ABRIL MAIO JUNHO JULHO AGOSTO SETEMBRO OUTUBRO NOVEMBRO DEZEMBRO".split()


def positional_kind(text):
    compact = re.sub(r"\s+", "", upper(text))
    if all(key in compact for key in ("LOTE", "ORI.", "DATA", "HISTORICO", "LANCAMENTO", "SALDOANTERIOR")):
        return "lot_origin"
    if all(
        marker in compact for marker in ("BANRISUL", "DIAHISTORICO", "DOCUMENTO", "MOVIMENTOSDACONTACORRENTE", "SALDOANTEM", "SALDONADATA")
    ):
        return "banrisul_month"
    return None


def _signed(cell):
    text = re.sub(r"\s+", "", cell.text).translate(str.maketrans({"−": "-", "—": "-", "–": "-"}))
    match = _SIGNED.fullmatch(text)
    if not match or match[1] and match[3]:
        fail("Um valor impresso está incompleto ou contém sinais conflitantes.")
    return money(match[2]) * (-1 if "-" in (match[1], match[3]) else 1)


def _is_money(cell):
    text = re.sub(r"\s+", "", cell.text).translate(str.maketrans({"−": "-", "—": "-", "–": "-"}))
    return bool(_SIGNED.fullmatch(text))


def parse_lot_origin(source):
    columns = _headers(source.pages[0], ("LOTE", "ORI.", "DATA", "HISTORICO", "DOC", "LANCAMENTO", "SALDO"))
    if not columns:
        fail("Não foi possível identificar as colunas de lote, origem e movimento.")
    first = source.pages[0]
    header_y = next(line.bbox["top"] for line in first.lines if upper(line.text) == "LOTE")
    emissions = [line for line in first.lines if line.bbox["top"] < header_y and re.fullmatch(DATE, line.text)]
    if len(emissions) != 1:
        fail("O ano dos movimentos abreviados não pode ser confirmado.")
    year = int(day(emissions[0].text)[:4])
    boundary = (columns["LANCAMENTO"] + columns["SALDO"]) / 2
    opening_cells = [line for line in first.lines if line.bbox["top"] < header_y and line.bbox["left"] >= boundary and _is_money(line)]
    if len(opening_cells) != 1:
        fail("O saldo anterior impresso não pode ser associado.")
    opening = running = _signed(opening_cells[0])
    rows, checks, final_control = [], 0, None
    for page in source.pages:
        stop = min(
            (line.bbox["top"] for line in page.lines if upper(line.text).startswith(("COMPOSICAO DO SALDO", "JUROS DE CHEQUE"))), default=1
        )
        anchors = [line for line in page.lines if line.bbox["top"] < stop and _LOT.fullmatch(line.text)]
        cash = [
            line
            for line in page.lines
            if (header_y if page is first else 0) < line.bbox["top"] < stop
            and line.bbox["left"] >= columns["LANCAMENTO"] - 0.04
            and _is_money(line)
        ]
        assigned = {line.id: nearest_row(line, anchors) for line in cash}
        used = set()
        for anchor in sorted(anchors, key=lambda line: line.bbox["top"]):
            match = _LOT.fullmatch(anchor.text)
            date_text = match[3]
            if len(date_text) == 8:
                candidate_year = year // 100 * 100 + int(date_text[-2:])
                if abs(candidate_year - year) > 1:
                    fail("O ano abreviado não confere com a emissão impressa.")
                date_text = date_text[:-2] + str(candidate_year)
            date = day(date_text)
            if rows and date < rows[-1].transaction.date:
                fail("A ordem das datas do extrato não pode ser confirmada.")
            values = [line for line in cash if assigned[line.id] is anchor]
            amounts = [line for line in values if line.bbox["left"] < boundary]
            balances = [line for line in values if line.bbox["left"] >= boundary]
            if len(amounts) != 1 or len(balances) > 1:
                fail("Um movimento está sem valor ou possui mais de um saldo associado.")
            amount = _signed(amounts[0])
            running += amount
            balance = _signed(balances[0]) if balances else None
            if balance is not None:
                if balance != running:
                    financial_mismatch()
                checks += 1
            description = match[4]
            reference = None
            words = [
                w
                for w in page.words
                if w.id in anchor.word_ids
                and w.bbox
                and columns["DOC"] - 0.025 <= w.bbox["left"] < columns["LANCAMENTO"] - 0.02
                and re.fullmatch(r"\d+", w.text)
            ]
            if len(words) > 1:
                fail("Uma referência de documento é ambígua.")
            if words:
                reference = words[0].text
                description = re.sub(r"\s+" + re.escape(reference) + r"$", "", description)
            rows.append(
                _source(_row(page.page_number, anchor.line_index, date, description, amount, balance, reference), page, [anchor, *values])
            )
            used.update(line.id for line in values)
            final_control = balance
        if used != {line.id for line in cash}:
            fail("Há um valor impresso sem movimento associado.")
    if not rows or final_control is None:
        fail("O último movimento está sem seu controle de saldo impresso.")
    return VerifiedStatement(
        rows, "lot_origin_monthly_cash_v1", opening, final_control, printed_balance_checks=checks, candidate_rows=len(rows)
    )


def parse_banrisul_month(source):
    text = upper("\n".join(line.text for page in source.pages for line in page.lines))
    if positional_kind(text) != "banrisul_month":
        return None
    periods = set()
    for month, year in re.findall(r"(?:PERIODO:\s*|MOVIMENTOS\s+)([A-Z]+)/\s*(\d{4})", text):
        matches = [i + 1 for i, name in enumerate(_MONTHS) if month in {name, name[:3]}]
        if len(matches) == 1:
            periods.add((matches[0], int(year)))
    if len(periods) != 1:
        fail("O mês e o ano dos dias abreviados não podem ser confirmados.")
    month, year = periods.pop()
    seeds = [(page, line) for page in source.pages for line in page.lines if re.fullmatch(rf"SALDO ANT EM\s+{DATE}", upper(line.text))]
    if len(seeds) != 1:
        fail("O saldo de abertura mensal não pode ser identificado.")
    seed_page, seed = seeds[0]
    history_x = seed.bbox["left"]
    doc_headers = [cell for page in source.pages for cell in (*page.lines, *page.words) if upper(cell.text) == "DOCUMENTO" and cell.bbox]
    doc_x = doc_headers[0].bbox["left"] if doc_headers else None
    if doc_x is None or doc_x <= history_x:
        fail("A coluna de documentos não pode ser identificada.")
    rows, running, opening, closing, checks = [], None, None, None, 0
    current_day = None
    for page in source.pages:
        top = seed.bbox["top"] - 0.01 if page is seed_page else 0
        stop = min((line.bbox["top"] for line in page.lines if "EMITIDO AS" in upper(line.text)), default=1)
        body = [line for line in page.lines if line.bbox and top <= line.bbox["top"] < stop]
        docs = [
            line
            for line in body
            if abs(line.bbox["left"] - doc_x) < 0.035 and re.fullmatch(r"[A-Z0-9]{4,20}", upper(line.text)) and re.search(r"\d", line.text)
        ]
        controls = [line for line in body if upper(line.text) == "SALDO NA DATA" or line is seed]
        anchors = sorted([*docs, *controls], key=lambda line: line.bbox["top"])
        cash = [line for line in body if line.bbox["left"] > doc_x + 0.08 and _is_money(line)]
        # Anything financial-looking in the value column must be accounted for,
        # including malformed digits/signs which cannot be silently omitted.
        invalid = [
            line for line in body if line.bbox["left"] > doc_x + 0.08 and re.search(r"\d[,\d.]*,", line.text) and not _is_money(line)
        ]
        if invalid:
            fail("Há um valor mensal com dígitos ou sinal ilegíveis.")
        assigned = {line.id: nearest_row(line, anchors) for line in cash}
        days = sorted(
            [line for line in body if line.bbox["left"] < history_x - 0.015 and re.fullmatch(r"\d{2}", line.text)],
            key=lambda line: line.bbox["top"],
        )
        used = set()
        for index, anchor in enumerate(anchors):
            for label in days:
                if label.bbox["top"] <= anchor.bbox["top"] + 0.006:
                    current_day = label.text
            values = [line for line in cash if assigned[line.id] is anchor]
            if len(values) != 1:
                fail("Um lançamento ou saldo mensal está sem valor inequívoco.")
            value = _signed(values[0])
            used.add(values[0].id)
            if anchor is seed:
                opening = running = value
                continue
            if upper(anchor.text) == "SALDO NA DATA":
                if running is None:
                    fail()
                if value != running:
                    financial_mismatch()
                closing = value
                checks += 1
                continue
            if current_day is None:
                fail("Um movimento está sem o dia impresso de referência.")
            date = day(f"{current_day}/{month:02d}/{year}")
            if rows and date < rows[-1].transaction.date:
                fail("A sequência de dias do extrato mensal não confere.")
            high = anchors[index + 1].bbox["top"] - 0.006 if index + 1 < len(anchors) else stop
            history = [
                line
                for line in body
                if history_x - 0.025 <= line.bbox["left"] < doc_x - 0.03
                and anchor.bbox["top"] - 0.006 <= line.bbox["top"] < high
                and not upper(line.text).startswith("MOVIMENTOS ")
            ]
            if not history or running is None:
                fail("O histórico de um movimento mensal não pode ser associado.")
            description = " ".join(line.text for line in sorted(history, key=lambda line: line.bbox["top"]))
            running += value
            rows.append(
                _source(
                    _row(page.page_number, anchor.line_index, date, description, value, None, anchor.text),
                    page,
                    [anchor, *history, *values],
                )
            )
            closing = None
        if used != {line.id for line in cash}:
            fail("Há um valor mensal sem movimento ou controle associado.")
    if not rows or closing is None:
        fail("O fechamento do extrato mensal não pode ser confirmado.")
    rows[-1] = replace(rows[-1], running_balance=float(closing))
    return VerifiedStatement(
        rows, "banrisul_monthly_cash_columns_v1", opening, closing, printed_balance_checks=checks, candidate_rows=len(rows)
    )
