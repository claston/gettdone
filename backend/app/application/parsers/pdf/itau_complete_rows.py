"""Original cell geometry for the complete Itaú web statement."""
import re
from contextlib import closing
from statistics import median

from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.native_table_source import native_header_columns
from app.application.parsers.pdf.native_unicred_grid import _PDFIUM_LOCK
from app.application.parsers.pdf.verified_statements import DATE, NUMBER, day, fail, money
from app.application.source_regions import evidence_regions

_KEYS = ("DATA", "LANCAMENTOS", "RAZAO SOCIAL", "CNPJ/CPF", "VALOR (R$)", "SALDO (R$)")
_CONTROLS = {"SALDO ANTERIOR", "SALDO MOVIMENTACAO CONTA", "SALDO TOTAL DISPONIVEL DIA", "SALDO APLIC. AUT."}


def _center(item):
    return item.bbox["top"] + item.bbox["height"] / 2


def _page_counter(line):
    return (line.bbox and line.bbox["top"] > .9 and line.bbox["left"] > .7
            and re.fullmatch(r"(?:(?:PAGINA|FOLHA)\s+)?\d+\s*(?:/|DE)\s*\d+", upper(line.text)))


def _rules(raw):
    """Cell borders can be separate paths, one for each column."""
    import pypdfium2 as pdfium

    result = []
    with _PDFIUM_LOCK, pdfium.PdfDocument(raw) as document:
        for index in range(len(document)):
            with closing(document[index]) as page:
                width, height = page.get_size()
                levels = {}
                for obj in page.get_objects(filter=[pdfium.raw.FPDF_PAGEOBJ_PATH]):
                    left, bottom, right, top = obj.get_bounds()
                    if right-left > width*.01 and top-bottom < height*.003:
                        level = round(1-(top+bottom)/2/height, 3)
                        levels.setdefault(level, []).append((left/width, right/width))
                # Require horizontal coverage of the date, history and financial
                # columns, not a short underline in a counterparty's history.
                result.append(sorted(y for y, spans in levels.items()
                    if min(left for left, _ in spans) < .12 and max(right for _, right in spans) > .90
                    and sum(right-left for left, right in set(spans)) > .75))
    return result


def _fields(words, columns):
    boundaries = [columns["LANCAMENTOS"]-.004, columns["RAZAO SOCIAL"]-.004,
                  columns["CNPJ/CPF"]-.004, columns["VALOR (R$)"]-.03,
                  (columns["VALOR (R$)"]+columns["SALDO (R$)"])/2]
    groups = [[] for _ in range(6)]
    rows = []
    tolerance = median(word.bbox["height"] for word in words)*.35 if words else 0
    for word in sorted(words, key=_center):
        if not rows or abs(rows[-1][0]-_center(word)) > tolerance:
            rows.append((_center(word), []))
        rows[-1][1].append(word)
    for _, row in rows:
        for word in sorted(row, key=lambda word: word.bbox["left"]):
            groups[sum(word.bbox["left"] >= boundary for boundary in boundaries)].append(word.text)
    return [" ".join(group) for group in groups]


def read_itau_complete_rows(raw, source):
    rules = _rules(raw)
    records, columns, pending, finished = [], None, [], False
    for page in source.pages:
        if finished and page.lines:
            fail("Há outra página após o encerramento do extrato Itaú.")
        header = native_header_columns(page, _KEYS)
        if header:
            if pending:
                fail("Um histórico Itaú entre páginas está sem movimento associado.")
            columns = header
        summaries = [line for line in page.lines if upper(line.text) == "SALDO DA CONTA CORRENTE"]
        stops = [line.bbox["top"] for line in summaries]
        stops += [line.bbox["top"] for line in page.lines
                  if upper(line.text).startswith(("AVISO:", "ATUALIZADO EM", "EM CASO DE DUVIDAS"))]
        stop = min(stops, default=1)
        counters = {identifier for line in page.lines if _page_counter(line) for identifier in line.word_ids}
        words = [word for word in page.words if word.bbox and _center(word) < stop and word.id not in counters]
        if header:
            headings = [word for word in words if upper(word.text) == "DATA"
                        and abs(word.bbox["left"]-header["DATA"]) < .004]
            low = max(_center(word) for word in headings) + .004
        else:
            low = 0
        words = [word for word in words if _center(word) > low]
        if not columns:
            fail("A tabela Itaú está sem cabeçalho de colunas confirmado.")
        dates = [word for word in words if word.bbox["left"] < columns["LANCAMENTOS"]-.004
                 and re.fullmatch(DATE, word.text)]
        if any(word.bbox["left"] < columns["LANCAMENTOS"]-.004
               and re.match(r"\d{2}/", word.text) and not re.fullmatch(DATE, word.text) for word in words):
            fail("Uma data Itaú está ilegível na página de continuação.")
        dates.sort(key=_center)
        used = set()
        for index, anchor in enumerate(dates):
            y = _center(anchor)
            above = [cut for cut in rules[page.page_number-1] if low < cut < y]
            below = [cut for cut in rules[page.page_number-1] if y < cut < stop]
            top = max(above) if above else (_center(dates[index-1])+y)/2 if index else low
            bottom = min(below) if below else (y+_center(dates[index+1]))/2 if index+1 < len(dates) else stop
            band = [word for word in words if top < _center(word) < bottom]
            if any(word.id in used for word in band):
                fail("As linhas da tabela Itaú se sobrepõem.")
            used.update(word.id for word in band)
            fields = _fields(band, columns)
            if fields[0] != anchor.text:
                fail("Uma data Itaú não pode ser associada à sua linha.")
            if pending:
                if index or header or fields[4] == fields[5] == "":
                    fail("Um histórico Itaú entre páginas não pode ser associado.")
                for field in range(1, 4):
                    fields[field] = " ".join([*(part[field] for _, _, part in pending), fields[field]]).strip()
            control = upper(fields[1]) in _CONTROLS
            token = fields[5] if control else fields[4]
            if not fields[1] or (fields[4] if control else fields[5]) or not re.fullmatch(rf"[+-]?\s*{NUMBER}", token):
                fail("Uma linha Itaú está sem descrição, valor ou saldo inequívoco na coluna impressa.")
            refs = [(page.page_number, line.line_index) for line in page.lines
                    if set(line.word_ids) & {word.id for word in band}]
            regions = evidence_regions(page.page_number, band)
            for previous_page, previous_words, _ in pending:
                refs.extend((previous_page.page_number, line.line_index) for line in previous_page.lines
                            if set(line.word_ids) & {word.id for word in previous_words})
                regions.extend(evidence_regions(previous_page.page_number, previous_words))
            pending = []
            records.append({"date": day(anchor.text), "label": upper(fields[1]), "fields": fields[1:4],
                            "control": control, "value": money(token.replace(" ", "")),
                            "page": page.page_number, "line": refs[0][1], "refs": refs, "regions": regions})
        leftovers = [word for word in words if word.id not in used]
        if leftovers and (dates or header or not summaries):
            # A ruled row may start at the bottom of a page and receive its date
            # and amount on the next. Only history columns can be carried.
            last_close = min((cut for cut in rules[page.page_number-1]
                              if dates and cut > _center(dates[-1])), default=None)
            tail = [word for word in leftovers if last_close is not None and _center(word) > last_close]
            fields = _fields(tail, columns)
            if tail and not fields[0] and not fields[4] and not fields[5] and len(tail) == len(leftovers):
                pending.append((page, tail, fields))
            else:
                fail("Há dados Itaú sem data ou movimento associado em uma página de continuação.")
        if summaries:
            if pending:
                fail("O último histórico Itaú está sem movimento associado.")
            finished = True
    if pending or not finished or not records:
        fail("A tabela completa Itaú não pôde ser delimitada.")
    return records
