"""Route recognized native statement grids using their original glyph evidence."""
import re
from dataclasses import replace

from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.native_table_source import read_native_table_source
from app.application.parsers.pdf.verified_statements import DATE, day, fail


def _kind(text):
    compact = re.sub(r"[\s-]+", "", upper(text))
    if all(marker in compact for marker in ("NDDIP", "CONTADIGITAL", "TOTALCREDITO", "TOTALDEBITO", "QUANTIDADEDEOPERACOES")):
        return "ndd"
    if all(marker in compact for marker in ("BTG", "CONTACORRENTEPJ", "ENTRADAS/SAIDAS(R$)", "SALDODEABERTURA")):
        return "btg"
    if all(marker in compact for marker in ("UNIPRIME", "COOPERATIVA", "EXTRATODE", "SALDOANTERIOR", "HISTORICO", "VALOR(R$)")):
        return "uniprime"
    if all(marker in compact for marker in ("CLIENTECONTAATUAL", "BALANCETE", "MOVIMENTO", "AG.ORIGEM", "VALORR$", "SALDO")):
        return "bb"
    if all(marker in compact for marker in ("INFINITEPAY", "HORA", "VALOR(R$)", "TOTALDEENTRADAS", "TOTALDESAIDAS")):
        return "infinitepay"
    if all(marker in compact for marker in ("REVOLUT", "EXTRATOEMBRL", "RESUMODOSALDO", "VALORESDESCONTADOS", "VALORESRECEBIDOS")):
        return "revolut"
    return None


def parse_followup_native_tables(raw, page_texts):
    kind = _kind("\n".join(page_texts))
    if kind is None:
        return None
    source = read_native_table_source(raw)
    if kind == "ndd":
        from app.application.parsers.pdf.ndd_native import parse_ndd_native

        statement = parse_ndd_native(source)
    elif kind == "btg":
        from app.application.parsers.pdf.btg_pj_ocr import parse_btg_pj

        # Canonicalize header spelling only; every box and word stays attached
        # to the source glyphs. Content and financial tokens are not rewritten.
        aliases = {re.sub(r"\s+", "", value): value for value in (
            "ENTRADAS / SAIDAS (R$)", "SALDO (R$)", "DATA LANCAMENTO", "DESCRICAO DO LANCAMENTO")}
        pages = [replace(page, lines=[replace(line, text=aliases.get(re.sub(r"\s+", "", upper(line.text)), line.text))
                                       for line in page.lines]) for page in source.pages]
        source = replace(source, pages=pages)
        statement = parse_btg_pj(source, native=True)
    elif kind == "bb":
        from app.application.parsers.pdf.bb_cash_ocr import parse_bb_cash_ocr

        statement = parse_bb_cash_ocr(source)
    elif kind == "uniprime":
        statement, source = _uniprime(source)
    elif kind == "revolut":
        from app.application.parsers.pdf.revolut_native import parse_revolut_native

        statement = parse_revolut_native(source)
    else:
        from app.application.parsers.pdf.infinitepay_native import parse_infinitepay_native

        statement = parse_infinitepay_native(source)
    if statement is None:
        fail("Não foi possível reconhecer as colunas do extrato nativo.")
    return statement, source


def _uniprime(source):
    from app.application.parsers.pdf.uniprime_ocr import parse_uniprime

    text = upper("\n".join(line.text for page in source.pages for line in page.lines))
    periods = set(re.findall(rf"EXTRATO DE\s+({DATE})\s+A\s+({DATE})", text))
    if len(periods) != 1:
        fail()
    first, last = next(iter(periods))
    start, end = day(first), day(last)
    recovered, pages = set(), []
    for page in source.pages:
        words = {word.id: word for word in page.words}
        history_headers = [line for line in page.lines if upper(line.text) == "HISTORICO"]
        history_header = history_headers[0] if len(history_headers) == 1 else None
        lines = []
        for line in page.lines:
            date_prefix = re.match(r"^(\d{2}/\d{2}/(?:\d{1,3}(?:\.{3}|…)|\d{4}))\s+.+", line.text)
            cells = [words[key] for key in line.word_ids]
            if date_prefix and len(cells) > 1 and cells[0].text == date_prefix[1]:
                rest = cells[1:]
                left = min(word.bbox["left"] for word in rest)
                right = max(word.bbox["left"] + word.bbox["width"] for word in rest)
                lines.append(replace(line, id=line.id + ":ref", text=" ".join(word.text for word in rest),
                    bbox={**line.bbox, "left": left, "width": right - left}, word_ids=tuple(word.id for word in rest)))
                line = replace(line, id=line.id + ":date", text=cells[0].text, bbox=cells[0].bbox, word_ids=(cells[0].id,))
            normalized = re.sub(r"\s+", "", upper(line.text))
            if normalized.startswith("VALOR(R$)SALDO("):
                cells = [words[key] for key in line.word_ids]
                split = next((index for index, word in enumerate(cells) if upper(word.text).startswith("SALDO")), None)
                if split is None:
                    fail()
                for label, group in (("VALOR (R$)", cells[:split]), ("SALDO (R$)", cells[split:])):
                    left = min(word.bbox["left"] for word in group)
                    right = max(word.bbox["left"] + word.bbox["width"] for word in group)
                    lines.append(replace(line, id=line.id + label, text=label,
                        bbox={**line.bbox, "left": left, "width": right - left}, word_ids=tuple(word.id for word in group)))
                continue
            if normalized.startswith("SALDO("):
                line = replace(line, text="SALDO (R$)")
            clipped = re.fullmatch(r"(\d{2}/\d{2})/(\d{1,3})(?:\.{3}|…)", line.text)
            if clipped:
                eligible = []
                for year in range(int(start[:4]), int(end[:4]) + 1):
                    if not str(year).startswith(clipped[2]):
                        continue
                    value = f"{clipped[1]}/{year}"
                    if start <= day(value) <= end:
                        eligible.append(value)
                if len(eligible) != 1:
                    fail("O ano truncado Uniprime não pode ser confirmado pelo período.")
                recovered.add((page.page_number, line.line_index))
                line = replace(line, text=eligible[0])
            if (history_header and line.bbox["top"] > history_header.bbox["top"]
                    and line.bbox["left"] < history_header.bbox["left"] - .015
                    < line.bbox["left"] + line.bbox["width"]):
                cells = [words[key] for key in line.word_ids]
                groups = ([word for word in cells if word.bbox["left"] < history_header.bbox["left"] - .015],
                          [word for word in cells if word.bbox["left"] >= history_header.bbox["left"] - .015])
                if all(groups):
                    for role, group in zip(("reference", "history"), groups, strict=True):
                        left = min(word.bbox["left"] for word in group)
                        right = max(word.bbox["left"] + word.bbox["width"] for word in group)
                        lines.append(replace(line, id=line.id + role, text=" ".join(word.text for word in group),
                            bbox={**line.bbox, "left": left, "width": right - left}, word_ids=tuple(word.id for word in group)))
                    continue
            lines.append(line)
        pages.append(replace(page, lines=lines))
    source = replace(source, pages=pages)
    statement = parse_uniprime(source)
    if statement is None:
        fail()
    statement.rows = [replace(row, warnings=[*row.warnings, "manual_review_recommended", "date_year_from_statement_period"])
                      if recovered.intersection(row.source_line_refs) else row for row in statement.rows]
    return statement, source
