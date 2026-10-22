"""Coverage and printed financial controls for explicit OCR statement structures."""

import re
from dataclasses import replace
from decimal import Decimal

from app.application.errors import PdfFinancialValidationError
from app.application.normalization.text import normalize_upper_text
from app.application.parsers.pdf.verified_statements import NUMBER, VerifiedStatement, _row, day, fail, money
from app.application.source_regions import evidence_regions


def parse_controlled_ocr(extraction):
    from app.application.parsers.pdf.bb_cash_ocr import is_bb_cash_ocr, parse_bb_cash_ocr
    from app.application.parsers.pdf.infinitepay_ocr import parse_infinitepay
    from app.application.sweep_statement_detection import INFINITEPAY, identify_sweep_statement

    if identify_sweep_statement("\n".join(line.text for page in extraction.pages for line in page.lines)) == INFINITEPAY:
        return parse_infinitepay(extraction)
    from app.application.parsers.pdf.bradesco_monthly_ocr import parse_bradesco_monthly
    from app.application.parsers.pdf.caixa_manager_ocr import is_caixa_manager, parse_caixa_manager
    from app.application.structured_statement_detection import BRADESCO_MONTHLY_OCR, is_bradesco_monthly_ocr

    if is_bradesco_monthly_ocr("\n".join(line.text for page in extraction.pages for line in page.lines)):
        try:
            return parse_bradesco_monthly(extraction)
        except PdfFinancialValidationError as exc:
            observability = dict(getattr(exc, "_parse_observability", {}) or {})
            observability.update(layout_inference_name=BRADESCO_MONTHLY_OCR, layout_inference_confidence=.99,
                                 selected_parser="layout_specific_bradesco_monthly_ocr")
            setattr(exc, "_parse_observability", observability)
            raise
    if is_caixa_manager(extraction):
        return parse_caixa_manager(extraction)
    texts = ["\n".join(line.text for line in page.lines) for page in extraction.pages]
    text = normalize_upper_text("\n".join(texts))
    if is_bb_cash_ocr(text):
        return parse_bb_cash_ocr(extraction)
    if "SICOOB SISBR" in text and "EXTRATO DE CONTA CORRENTE" in text:
        from app.application.parsers.pdf.sicoob_sisbr_ocr import parse_sicoob

        return parse_sicoob(extraction)
    from app.application.parsers.pdf.followup_ocr import (
        parse_banrisul_columns,
        parse_bradesco_columns,
        parse_stone_credit_debit,
    )
    if "BVP-PORTAL BANRISUL" in text:
        return parse_banrisul_columns(extraction)
    if "BRADESCO" in text and "EXTRATO MENSAL / POR PERIODO" in text and "NET EMPRESA" in text:
        return parse_bradesco_columns(extraction)
    if "STONE" in text and "EXTRATO DE CONTA CORRENTE" in text and "LANCAMENTO" in text:
        return parse_stone_credit_debit(extraction)
    if "SICREDI" in text and "EXTRATO DE CONTA CORRENTE" in text:
        if re.search(r"CONTINUA NA PAGINA\s+\d+", normalize_upper_text(texts[-1])):
            return _sicredi_fragment(extraction)
    if "STONE" in text and "EXTRATO DE CONTA CORRENTE" in text:
        counters = [re.findall(r"PAGINA\s+(\d+)\s+DE\s+(\d+)", normalize_upper_text(page)) for page in texts]
        if counters and any(counters):
            total = int(next(c[0][1] for c in counters if c))
            first = int(next(c[0][0] for c in counters if c))
            if counters != [[(str(i), str(total))] for i in range(first, first + len(counters))]:
                fail(
                    "Falta uma página entre os trechos do extrato Stone. "
                    "Envie páginas consecutivas para conferir os saldos nas quebras de página."
                )
    if "BCO ITAU EXTRATO CTA CORRENTE" in text and "DIAHISTORICO" in text:
        return _itau(extraction)
    if "CAIXA" in text and "REM BASICA" in text and "CRED JUROS" in text:
        return _caixa(extraction)
    return None


def _sicredi_fragment(extraction):
    rows = []
    opening = running = None
    checked = 0
    for page in extraction.pages:
        lines = sorted(page.lines, key=lambda line: line.line_index)
        labels = {normalize_upper_text(line.text): line for line in lines if line.bbox}
        if not all(key in labels for key in ("DEBITO", "CREDITO", "SALDO", "SALDO ANTERIOR")):
            fail("Não foi possível localizar as colunas da página Sicredi. Envie o trecho completo e legível.")
        debit, credit, balance_column = [labels[k].bbox["left"] for k in ("DEBITO", "CREDITO", "SALDO")]
        credit_left, balance_left = (debit + credit) / 2, (credit + balance_column) / 2
        opening_label = labels["SALDO ANTERIOR"]
        seeds = [
            item
            for item in lines
            if item.bbox
            and item.bbox["left"] > balance_left
            and abs(item.bbox["top"] - opening_label.bbox["top"]) < 0.004
            and re.fullmatch(NUMBER, item.text)
        ]
        if len(seeds) != 1:
            fail()
        seed = money(seeds[0].text)
        if opening is None:
            opening = running = seed
        elif running != seed:
            fail()
        anchors = [item for item in lines if re.fullmatch(r"\d{2}/\d{2}/\d{4}", item.text) and item.bbox and item.bbox["left"] < debit]
        for i, anchor in enumerate(anchors):
            upper = anchors[i + 1].line_index if i + 1 < len(anchors) else 10**9
            cells = [item for item in lines if anchor.line_index <= item.line_index < upper and item.bbox]
            financial = [item for item in cells if item.bbox["left"] > debit - 0.04 and re.fullmatch(NUMBER, item.text)]
            values = [item for item in financial if item.bbox["left"] < balance_left]
            balances = [item for item in financial if item.bbox["left"] >= balance_left]
            if len(values) != 1 or len(balances) > 1:
                fail()
            value = values[0]
            amount = money(value.text) * (-1 if value.bbox["left"] < credit_left else 1)
            running += amount
            if balances:
                if running != money(balances[0].text):
                    fail()
                checked += 1
            description = " ".join(item.text for item in cells if anchor.bbox["left"] + 0.03 < item.bbox["left"] < debit - 0.05)
            date = day(anchor.text)
            if rows and date < rows[-1].transaction.date:
                fail()
            row = _row(page.page_number, anchor.line_index, date, description, amount, running if balances else None)
            rows.append(_provenance(row, page, cells))
    if not rows or not checked:
        fail()
    return VerifiedStatement(rows, "sicredi_ocr_columns_partial_v1", opening, running, partial=True)


def _provenance(row, page, items):
    return replace(
        row,
        source_regions=evidence_regions(page.page_number, items, words=page.words),
        source_line_refs=tuple((page.page_number, line.line_index) for line in items),
    )


def _itau(extraction):
    months = ("JANEIRO", "FEVEREIRO", "MARCO", "ABRIL", "MAIO", "JUNHO", "JULHO", "AGOSTO", "SETEMBRO", "OUTUBRO", "NOVEMBRO", "DEZEMBRO")
    text = normalize_upper_text("\n".join(line.text for p in extraction.pages for line in p.lines))
    periods = re.findall(r"\b(" + "|".join(months) + r")/(\d{4})\b", text)
    if not periods:
        fail()
    month, year = months.index(periods[0][0]) + 1, int(periods[0][1])
    rows = []
    opening = running = None
    current_day = None
    checks = 0
    last_control_count = 0
    column = None
    pattern = re.compile(rf"^({NUMBER}|\d+\.\d{{2}})(-?)$")
    for page in extraction.pages:
        lines = [line for line in page.lines if line.bbox]
        headers = [line for line in lines if normalize_upper_text(line.text) == "VALOR"]
        if len(headers) > 1 or not headers and column is None:
            fail()
        if headers:
            column = headers[0].bbox["left"]
        table_top = headers[0].bbox["top"] if headers else -1
        amounts = sorted(
            [
                line
                for line in lines
                if line.bbox["left"] >= column - 0.03 and line.bbox["top"] > table_top and pattern.fullmatch(line.text.strip())
            ],
            key=lambda line: line.bbox["top"],
        )
        descriptions = [
            line
            for line in lines
            if line.bbox["left"] < column - 0.06
            and line.bbox["top"] > table_top
            and (
                re.match(r"^\d{2}\s+\S", line.text)
                or re.match(r"^(REDE|VISA|PIX|SDO|SISPAG|BOLETO|TAR|REND)\b", normalize_upper_text(line.text))
            )
        ]
        used = set()
        for cell in amounts:
            near = sorted(descriptions, key=lambda line: abs(line.bbox["top"] - cell.bbox["top"]))
            if not near or abs(near[0].bbox["top"] - cell.bbox["top"]) > 0.008 or near[0].id in used:
                fail("Não foi possível associar cada valor à sua linha do extrato Itaú. Envie uma digitalização mais nítida.")
            source = near[0]
            used.add(source.id)
            label = normalize_upper_text(source.text)
            match = pattern.fullmatch(cell.text.strip())
            raw = match[1]
            amount = money(raw) if "," in raw else Decimal(raw)
            if match[2]:
                amount = -amount
            if "SALDO ANTERIOR" in label:
                if opening is not None or rows:
                    fail()
                opening = running = amount
                continue
            date_match = re.match(r"^(\d{2})\s+(.+)", source.text)
            if date_match:
                current_day = int(date_match[1])
                description = date_match[2]
                # OCR occasionally places two day cells on the following line.
                description = re.sub(r"^\d{2}\s+", "", description)
            else:
                description = source.text
            if current_day is None or running is None:
                fail()
            date = day(f"{current_day:02}/{month:02}/{year}")
            if rows and date < rows[-1].transaction.date:
                fail()
            if "SDO CTA/APL AUTOMATICAS" in label:
                if amount != running:
                    fail(
                        "Os lançamentos lidos pelo OCR não fecham com o saldo diário do Itaú. "
                        "Envie uma imagem mais nítida ou o PDF original."
                    )
                if rows:
                    rows[-1] = replace(rows[-1], running_balance=float(running))
                checks += 1
                last_control_count = len(rows)
                continue
            running += amount
            row = _row(page.page_number, source.line_index, date, description, amount)
            rows.append(_provenance(row, page, [source, cell]))
        if len(used) != len(descriptions):
            fail("Uma linha do Itaú ficou sem valor associado. Envie o extrato completo e legível.")
    if not rows or not checks or last_control_count != len(rows):
        fail()
    return VerifiedStatement(rows, "itau_receipt_ocr_daily_controls_v1", opening, running)


def _caixa(extraction):
    rows = []
    opening = running = None
    checks = 0
    amount_pattern = re.compile(rf"^({NUMBER})\s*([CD]?)$", re.I)
    for page in extraction.pages:
        lines = [line for line in page.lines if line.bbox]
        anchors = sorted(
            [line for line in lines if re.fullmatch(r"\d{6}", line.text.strip()) and line.bbox["left"] < 0.4],
            key=lambda line: line.bbox["top"],
        )
        if not anchors:
            fail()
        for i, anchor in enumerate(anchors):
            left = anchor.bbox["left"]
            # Textract reads these five-column rows in order. Index boundaries
            # preserve tilted rows that overlap the next row geometrically.
            lower = anchor.line_index - 1
            upper = anchors[i + 1].line_index - 1 if i + 1 < len(anchors) else 10**9
            cells = [line for line in lines if lower <= line.line_index < upper]
            dates = [line for line in cells if line.bbox["left"] < left - 0.02]
            dates = [line for line in dates if "/" in line.text]
            if len(dates) != 1 or not re.fullmatch(r"\d{2}/\d{2}/\d{4}", dates[0].text.strip()):
                fail(
                    "A data de um lançamento da Caixa está cortada ou ilegível. "
                    "Envie a página inteira; o lançamento não será omitido nem terá sua data presumida."
                )
            date = day(dates[0].text.strip())
            description_cells = [line for line in cells if left + 0.04 < line.bbox["left"] < 0.55]
            description = " ".join(line.text for line in description_cells)

            def normalized_value(line):
                # A comma substituted for a thousands separator has an exact,
                # unambiguous spelling and must also pass adjacent balances.
                return re.sub(r"^(\d{1,3}),(\d{3},\d{2}\s*[CDcd]?)$", r"\1.\2", line.text.strip())

            values = [line for line in cells if line.bbox["left"] >= 0.55 and amount_pattern.fullmatch(normalized_value(line))]
            values.sort(key=lambda line: line.bbox["left"])
            if len(values) != 2 or values[1].bbox["left"] - values[0].bbox["left"] < 0.05:
                fail("Um valor ou saldo da Caixa está cortado ou ilegível. Envie a página inteira e nítida.")
            parsed = [amount_pattern.fullmatch(normalized_value(line)) for line in values]
            if not parsed[0][2] or not parsed[1][2] and opening is None:
                fail()
            amount, balance = [money(match[1]) * (-1 if match[2].upper() == "D" else 1) for match in parsed]
            if opening is None:
                opening = running = balance - amount
            if rows and date < rows[-1].transaction.date or running + amount != balance:
                fail("Os valores e saldos da Caixa não fecham entre as linhas do OCR. Envie uma imagem mais nítida.")
            running = balance
            checks += 1
            if "SALDO DIA" in normalize_upper_text(description):
                if amount:
                    fail()
                continue
            if not description:
                fail()
            row = _row(page.page_number, anchor.line_index, date, description, amount, balance, anchor.text)
            if any(normalized_value(line) != line.text.strip() for line in values):
                row = replace(row, warnings=["ocr_numeric_separator_normalized"])
            if not parsed[1][2]:
                row = replace(row, warnings=[*row.warnings, "ocr_balance_sign_validated_from_ledger"])
            rows.append(_provenance(row, page, [dates[0], anchor, *description_cells, *values]))
    if not rows or checks < len(rows):
        fail()
    return VerifiedStatement(rows, "caixa_ocr_columns_validated_v1", opening, running)
