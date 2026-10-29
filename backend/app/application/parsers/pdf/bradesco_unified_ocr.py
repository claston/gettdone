"""Credit/debit ledgers from unified scans, preserving each product's scope."""

import re
from decimal import Decimal

from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.financial_reports import _check, _event
from app.application.parsers.pdf.row_geometry import nearest_row, row_slope
from app.application.parsers.pdf.verified_statements import NUMBER, day, fail, money

_MONEY = re.compile(rf"({NUMBER}|\d+(?:\.\d{{3}})*\.\d{{2}})(-?)(?:\s*BX)?")
_PERIOD = re.compile(r"(\d{2}/\d{2}/\d{4})\s+a\s+(\d{2}/\d{2}/\d{4})", re.I)


def is_bradesco_unified(source):
    text = upper(" ".join(line.text for p in source.pages for line in p.lines))
    return "BRADESCO" in text and bool(re.search(r"EXTRATO UNIFICADO\s*-?\s*PESSOA JURIDICA", text))


def _value(cell):
    match = _MONEY.fullmatch(cell.text.strip())
    if not match:
        fail("Um valor Bradesco está sem dígitos ou sinal inequívocos.")
    value = money(match[1]) if "," in match[1] else Decimal("".join(match[1].split(".")[:-1]) + "." + match[1].split(".")[-1])
    return -value if match[2] else value


def parse_bradesco_unified(source):
    text = "\n".join(line.text for p in source.pages for line in p.lines)
    periods = set(_PERIOD.findall(text))
    if not periods:
        fail("As datas abreviadas do extrato unificado estão sem ano inequívoco.")
    current_period = next(iter(periods)) if len(periods) == 1 else None
    events, observations, validations, products = [], [], [], set()
    state = {}
    for page in source.pages:
        lines = [line for line in page.lines if line.bbox]
        page_periods = set(_PERIOD.findall(" ".join(line.text for line in lines)))
        if len(page_periods) > 1:
            fail("Há períodos conflitantes na mesma página do extrato unificado.")
        if page_periods:
            current_period = page_periods.pop()
        headers = [line for line in lines if upper(line.text) == "CREDITO"]
        if not headers:
            continue
        if current_period is None:
            fail("Uma tabela de datas abreviadas está sem seu período de referência.")
        start, end = current_period
        year = int(end[-4:])
        for credit in headers:
            header_y = credit.bbox["top"]
            band = [line for line in lines if abs(line.bbox["top"] - header_y) < 0.02]
            labels = {
                upper(line.text): line
                for line in band
                if upper(line.text) in {"DATA", "HISTORICO", "DOCTO", "DOCUMENTO", "INDICES", "CREDITO", "DEBITO", "SALDO"}
            }
            if not {"DATA", "HISTORICO", "CREDITO", "DEBITO", "SALDO"} <= labels.keys():
                continue
            document = labels.get("DOCTO") or labels.get("DOCUMENTO")
            if document is None:
                fail("A coluna de documentos do extrato unificado está ausente.")
            product = "savings" if "INDICES" in labels else "checking"
            products.add(product)
            ledger = state.setdefault(
                (product, current_period),
                {"opening": None, "running": None, "credit": Decimal(0), "debit": Decimal(0), "date": None, "closed": False},
            )
            if ledger["closed"]:
                fail("Um novo bloco financeiro aparece após o total da mesma conta.")
            slope = row_slope(list(labels.values()))

            def y(cell):
                return cell.bbox["top"] - slope * cell.bbox["left"]

            low = max(y(line) for line in labels.values()) + 0.003
            totals = [
                line
                for line in lines
                if upper(line.text) in {"TOTAL", "TOTAL DA MOVIMENTACAO"} and line.bbox["left"] < 0.35 and y(line) > low
            ]
            total = min(totals, key=y) if totals else None
            next_section = min(
                (y(line) for line in lines if upper(line.text) in {"INVESTIMENTOS", "FUNDOS", "POUPANCA FACIL"} and y(line) > low),
                default=0.90,
            )
            high = y(total) + 0.01 if total else next_section
            body = [line for line in lines if low < y(line) < high]
            doc_x = document.bbox["left"]
            credit_x = credit.bbox["left"] - 0.065
            debit_left = (credit.bbox["left"] + labels["DEBITO"].bbox["left"]) / 2
            balance_left = (labels["DEBITO"].bbox["left"] + labels["SALDO"].bbox["left"]) / 2
            docs = sorted([line for line in body if abs(line.bbox["left"] - doc_x) < 0.03 and re.fullmatch(r"\d{4,12}", line.text)], key=y)
            opening_labels = [line for line in body if upper(line.text) == "SALDO ANTERIOR"]
            history_left = min((line.bbox["left"] for line in opening_labels), default=0.14) - 0.012
            balances = [line for line in body if line.bbox["left"] >= balance_left and re.search(r"\d[,.]\d", line.text)]
            cash = [line for line in body if credit_x <= line.bbox["left"] < balance_left and re.search(r"\d[,.]\d", line.text)]
            anchors = [*docs, *opening_labels, *([total] if total else [])]
            assigned = {line.id: nearest_row(line, anchors, slope=slope, max_distance=0.018) for line in cash}
            if any(anchor is None for anchor in assigned.values()):
                fail("Há um valor de crédito ou débito sem documento associado.")
            if opening_labels:
                if len(opening_labels) != 1 or ledger["opening"] is not None:
                    fail()
                label = opening_labels[0]
                values = [line for line in balances if abs(y(line) - y(label)) < 0.012]
                if len(values) != 1:
                    fail("O saldo anterior da conta não pode ser separado.")
                ledger["opening"] = ledger["running"] = _value(values[0])
            if ledger["opening"] is None:
                validations.append(_check(f"{product}_opening_missing", "não disponível", "saldo anterior impresso"))
            date_cells = sorted([line for line in body if line.bbox["left"] < 0.13 and re.fullmatch(r"\d{2}/\d{2}", line.text)], key=y)
            for index, anchor in enumerate(docs):
                for label in date_cells:
                    if y(label) <= y(anchor) + 0.006:
                        date_year = year - 1 if start[-4:] != end[-4:] and int(label.text[3:]) > int(end[3:5]) else year
                        ledger["date"] = day(f"{label.text}/{date_year}")
                values = [line for line in cash if assigned[line.id] is anchor]
                if len(values) != 1 or ledger["date"] is None:
                    fail("Um lançamento está sem data, crédito ou débito próprios.")
                cell = values[0]
                amount = _value(cell) * (-1 if cell.bbox["left"] >= debit_left else 1)
                lower = y(anchor) - 0.007
                upper_y = y(docs[index + 1]) - 0.007 if index + 1 < len(docs) else y(total) - 0.003 if total else high
                history = [
                    line
                    for line in body
                    if history_left <= line.bbox["left"] < doc_x - 0.035
                    and lower <= y(line) < upper_y
                    and upper(line.text) != "SALDO ANTERIOR"
                ]
                if not history:
                    fail("O histórico de um lançamento está ilegível.")
                description = " ".join(line.text for line in sorted(history, key=y))
                if ledger["running"] is not None:
                    ledger["running"] += amount
                ledger["credit" if amount >= 0 else "debit"] += abs(amount)
                refs = [{"page": page.page_number, "line": line.line_index} for line in [anchor, *history, cell]]
                events.append(
                    _event(
                        "cash_movement",
                        dates={"transaction": ledger["date"]},
                        amounts={"movement": str(amount)},
                        primary="movement",
                        cash_effect=str(amount),
                        reference=anchor.text,
                        description=description,
                        refs=refs,
                        attributes={"product_account": product, "source_fields": {"value": cell.text, "description": description}},
                    )
                )
                next_y = y(docs[index + 1]) - 0.005 if index + 1 < len(docs) else y(total) - 0.005 if total else high
                controls = [line for line in balances if y(anchor) - 0.005 <= y(line) < next_y]
                for balance in controls:
                    validations.append(
                        _check(
                            f"{product}_running_balance",
                            ledger["running"] if ledger["running"] is not None else "não disponível",
                            _value(balance),
                            refs=[{"page": page.page_number, "line": balance.line_index}],
                        )
                    )
            if total:
                cells = [line for line in cash if assigned[line.id] is total]
                for role, left, right in (("credit", credit_x, debit_left), ("debit", debit_left, balance_left)):
                    values = [line for line in cells if left <= line.bbox["left"] < right]
                    if len(values) != 1:
                        fail("O total de créditos ou débitos está ausente.")
                    validations.append(_check(f"{product}_{role}_total", ledger[role], abs(_value(values[0]))))
                values = [line for line in balances if abs(y(line) - y(total)) < 0.012]
                if len(values) != 1:
                    fail("O saldo de encerramento da conta está ausente.")
                validations.append(_check(f"{product}_closing_balance", ledger["running"], _value(values[0])))
                observations.append(
                    {
                        "observation_type": "product_balance",
                        "dates": {},
                        "amounts": {
                            **({"opening": str(ledger["opening"])} if ledger["opening"] is not None else {}),
                            "closing": str(_value(values[0])),
                        },
                        "attributes": {"product_account": product, "period_start": day(start), "period_end": day(end)},
                        "source_refs": [{"page": page.page_number, "line": total.line_index}],
                    }
                )
                ledger["closed"] = True
    if not events:
        fail("O extrato unificado está sem movimentos financeiros legíveis.")
    for (product, period), ledger in state.items():
        if not ledger["closed"]:
            validations.append(_check(f"{product}_closing_missing", "não disponível", "controle de encerramento impresso"))
    events.sort(key=lambda e: e["dates"]["transaction"])
    multiple = len(products) > 1
    valid = all(v["status"] == "passed" for v in validations)
    ofx = valid and not multiple
    notice = (
        "Conta corrente e poupança estão identificadas separadamente. O Excel e o CSV preservam ambos os produtos. "
        "O OFX exige um extrato de uma única conta."
        if multiple
        else "Movimentações da conta corrente, separadas dos saldos e das posições de investimentos."
    )
    if any(not ledger["closed"] or ledger["opening"] is None for ledger in state.values()):
        notice += " Há controles de saldo ausentes; os movimentos legíveis foram preservados para revisão."
    return {
        "schema_version": "financial_events_v1",
        "document_type": "cash_flow_report",
        "evidence": ["bradesco_unified_product_columns"],
        "origin": "Bradesco",
        "representation": "ocr_text",
        "events": events,
        "observations": observations,
        "metadata": {"source_bank_code": "237"},
        "validations": validations,
        "notice": notice,
        "export": {
            "available_formats": ["csv", "xlsx", "ofx"] if ofx else ["csv", "xlsx"],
            "ofx_profile": None,
            "requires_destination_account": ofx,
            "supports_row_edits": False,
            "reason": notice,
        },
    }
