"""Dated cash grids: preserve signed amounts and separate daily balance controls."""
import re
from dataclasses import replace

from app.application.normalization.ocr_numeric_separators import normalize_ocr_numeric_separators
from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.followup_ocr import _source
from app.application.parsers.pdf.native_table_source import split_native_columns
from app.application.parsers.pdf.row_geometry import nearest_row
from app.application.parsers.pdf.statement_completeness import validate_statement_completeness
from app.application.parsers.pdf.statement_review import financial_mismatch
from app.application.parsers.pdf.verified_statements import DATE, NUMBER, VerifiedStatement, _row, day, fail, money


def consolidated_cash_kind(text):
    normalized = upper(text)
    if all(token in normalized for token in ("EXTRATO CONSOLIDADO DE CONTA CORRENTE", "IDENTIFICACAO", "CONTA INTEGRACAO")) or all(
            token in normalized for token in ("CRESOL", "EXTRATO CONSOLIDADO DE CONTA CORRENTE", "IDENTIFICACAO")):
        return "cresol"
    if all(token in normalized for token in ("MAREE INSTITUICAO DE PAGAMENTO", "TIPO DE TRANSACAO", "VALOR DA TRANSACAO")):
        return "maree"
    return None


def parse_consolidated_cash(source, kind):
    rows, opening, running, checks, scheduled = [], None, None, 0, 0
    closing = None
    non_cash = 0
    future = False
    for original in source.pages:
        # Financial column starts at its printed heading; amounts may extend
        # slightly left because they are right aligned.
        headings = [line for line in original.lines if line.bbox and upper(line.text) in {"VALOR", "VALOR DA TRANSACAO"}]
        if headings:
            value_left = headings[0].bbox["left"] - .06
        elif not rows:
            fail("A coluna de valor do extrato não pode ser identificada.")
        page = split_native_columns(original, [.18 if kind == "cresol" else .19, value_left])
        stop_labels = [line for line in page.lines if line.bbox and upper(line.text).startswith("LANCAMENTOS FUTUROS")]
        stop = min((line.bbox["top"] for line in stop_labels), default=2)
        header = max((line.bbox["top"] for line in headings), default=0)
        footer = min((line.bbox["top"]-.003 for line in original.lines if line.bbox and "GCC." in upper(line.text)), default=1)
        body = [line for line in page.lines if line.bbox and header+.005 < line.bbox["top"] < footer]
        closing_labels = [line for line in body if upper(line.text).replace(" ", "") == "(=)SALDO:"]
        if closing_labels:
            closing_line = closing_labels[0]
            closing_cells = [line for line in body if line.bbox["left"] >= value_left
                             and abs(line.bbox["top"]-closing_line.bbox["top"]) < .004]
            if len(closing_cells) == 1 and (match := re.fullmatch(rf"({NUMBER})\s*([CD])", upper(closing_cells[0].text))):
                closing = money(match[1]) * (-1 if match[2] == "D" else 1)
            body = [line for line in body if line.bbox["top"] < closing_line.bbox["top"]-.004
                    or line.bbox["top"] >= stop]
        date_pattern = DATE if kind == "cresol" else r"\d{2}-\d{2}-\d{4}"
        scheduled += sum(bool(re.match(rf"^{date_pattern}\b", line.text)) for line in original.lines
                         if line.bbox and (future or line.bbox["top"] >= stop))
        anchors = sorted([line for line in body if line.bbox["left"] < .18 and re.fullmatch(date_pattern, line.text)],
                         key=lambda line: line.bbox["top"])
        values = [line for line in body if line.bbox["left"] >= value_left and re.search(r"\d[,.]\d", line.text)]
        if not future:
            for line in body:
                if (line.bbox["top"] < stop and line.bbox["left"] < .18
                        and re.match(r"^\d{1,2}[/\-]", line.text) and not re.fullmatch(date_pattern, line.text)):
                    fail("Uma data de movimento está ilegível.")
            if any(nearest_row(line, anchors, max_distance=.012) is None
                   for line in values if line.bbox["top"] < stop):
                fail("Um valor está sem data legível; a linha não pode ser omitida.")
        for anchor in anchors:
            if future or anchor.bbox["top"] >= stop:
                continue
            cells = [line for line in values if nearest_row(line, anchors, max_distance=.012) is anchor]
            history = [line for line in body if .18 <= line.bbox["left"] < value_left
                       and nearest_row(line, anchors, max_distance=.012) is anchor]
            label = upper(" ".join(line.text for line in history))
            if len(cells) != 1 or not history:
                fail("Uma linha do extrato está sem valor ou histórico inequívoco.")
            token = normalize_ocr_numeric_separators(upper(cells[0].text).strip())
            detached_stroke = re.fullmatch(rf"({NUMBER})\s+[I|]\s+([CD])", token)
            if detached_stroke:
                token = f"{detached_stroke[1]} {detached_stroke[2]}"
            is_control = label.startswith("SALDO ANTERIOR") or label.startswith("SALDO DO DIA")
            if (re.fullmatch(rf"{NUMBER}\s*\*?", token)
                    and label.startswith("DEPOSITOS EM CHEQUES BLOQUEADOS")):
                non_cash += 1
                continue
            if kind == "maree":
                match = re.fullmatch(rf"([+-]?)\s*R\$\s*({NUMBER})", token)
                if not match:
                    fail("O sinal ou valor da transação não está legível.")
                amount = money(match[2]) * (-1 if match[1] == "-" else 1)
                inferred = False
            else:
                match = re.fullmatch(rf"({NUMBER})\s*([CDO€]?)", token)
                if not match:
                    fail("O valor C/D do extrato não está legível.")
                sign = match[2] if match[2] in {"C", "D"} else ""
                inferred = not sign
                if not sign:
                    # Explicit financial direction is independent of balance
                    # arithmetic. Never infer a missing sign merely to reconcile.
                    sign = ("C" if label.startswith(("CREDITO ", "PIX CREDITO", "TED CREDITO", "RESGATE DE INVESTIMENTO PARA C-C",
                                                    "DEPOSITO EM DINHEIRO", "DESBLOQUEIO DE CHEQUE DEPOSITADO"))
                            else "D" if label.startswith(("PIX DEBITO", "TED DEBITO", "APLICACAO PARA CONTA INVESTIMENTO",
                                "PAGAMENTO DE TITULOS IB", "COMPRAS NO DEBITO", "SAQUE NO CAIXA", "DEBITO CONVENIO")) else None)
                if sign is None:
                    if is_control:
                        financial_mismatch("Um saldo de controle está sem sinal C/D legível.")
                        continue
                    fail("Um movimento está sem sinal C/D ou direção explícita.")
                amount = money(match[1]) * (-1 if sign == "D" else 1)
            if is_control:
                if opening is None and not rows:
                    opening = running = amount
                elif running is not None:
                    checks += 1
                    if running != amount:
                        financial_mismatch()
                continue
            date = day(anchor.text, "%d/%m/%Y" if kind == "cresol" else "%d-%m-%Y")
            row = _source(_row(page.page_number, anchor.line_index, date, " ".join(line.text for line in history), amount, None),
                          page, [anchor, *history, *cells])
            if inferred:
                row = replace(row, warnings=[*row.warnings, "manual_review_recommended", "amount_sign_from_explicit_pix_label"])
            if detached_stroke:
                row = replace(row, warnings=[*row.warnings, "manual_review_recommended", "ocr_trailing_mark_confirmed"])
            rows.append(row)
            if running is not None:
                running += amount
        future = future or bool(stop_labels)
    if not rows:
        fail()
    if closing is not None and running is not None:
        checks += 1
        if running != closing:
            financial_mismatch()
    # A sum of movements is not an independently printed closing balance.
    partial = validate_statement_completeness(["\n".join(line.text for line in p.lines) for p in source.pages])
    return VerifiedStatement(rows, f"{kind}_signed_cash_columns_v1", opening, closing, partial=partial,
                             printed_balance_checks=checks, scheduled_entries_excluded=scheduled,
                             balance_bounds_unavailable=partial, non_cash_entries=non_cash)
