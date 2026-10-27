"""Lossless financial events for three explicit report structures.

Money is kept as decimal strings in the versioned contract. Banking projections
are adapters; fiscal issuance and investment positions are not cash movements.
"""
import re
from datetime import datetime
from decimal import Decimal

from app.application.errors import PdfFinancialValidationError
from app.application.financial_document_detection import (
    FISCAL_SUMMARY,
    INVESTMENT_STATEMENT,
    RECEIVABLES_REPORT,
    detect_financial_report,
)
from app.application.normalization.text import normalize_upper_text

MONEY = r"[+-]?(?:\d{1,3}(?:\.\d{3})+|\d+),\d{2}(?!\d)"
DATE = r"\d{2}/\d{2}/\d{4}"
MESSAGE = "Não foi possível associar todos os campos deste relatório. Envie o PDF original completo para preservar datas e valores."


def money(raw: str) -> str:
    return format(Decimal(raw.replace(".", "").replace(",", ".")), ".2f")


def date(raw: str) -> str:
    try:
        return datetime.strptime(raw, "%d/%m/%Y").date().isoformat()
    except ValueError as exc:
        raise PdfFinancialValidationError("O relatório contém uma data inválida. Confira o documento original.") from exc


def _event(kind, *, dates, amounts, primary, cash_effect, reference, description, refs, status="confirmed",
           counterparty=None, attributes=None):
    return {
        "event_id": f"event_p{refs[0]['page']}_l{refs[0]['line']}", "event_type": kind,
        "status": status, "reference": reference, "counterparty": counterparty, "dates": dates,
        "amounts": amounts, "amount": amounts[primary], "amount_basis": primary, "currency": "BRL",
        "cash_effect": cash_effect, "description": description, "source_refs": refs,
        "attributes": attributes or {},
    }


def _check(code, actual, expected, *, refs=None):
    return {"code": code, "status": "passed" if actual == expected else "failed",
            "actual": str(actual), "expected": str(expected), "source_refs": refs or []}


def _sum(rows, field):
    return sum((Decimal(row["amounts"].get(field, "0")) for row in rows), Decimal("0"))


def parse_financial_report_pages(page_texts: list[str]) -> dict | None:
    text = "\n".join(page_texts)
    detected = detect_financial_report(text)
    if detected is None:
        return None
    kind, evidence = detected
    if evidence == "xp_account_web_ledger":
        raise PdfFinancialValidationError("Envie o PDF original da conta XP para preservar suas duas datas e as colunas dos movimentos.")
    if evidence == "xp_investment_account_ledger":
        from app.application.parsers.pdf.xp_investment_cash import parse_xp_investment_pages

        return parse_xp_investment_pages(page_texts)
    if evidence == "pagbank_sales_gross_fee_net_status":
        from app.application.parsers.pdf.acquirer_sales import parse_sales_pages

        return parse_sales_pages(page_texts)
    if evidence == "caixa_monthly_cdb_positions":
        error = PdfFinancialValidationError(
            "Este documento informa posições de CDB da Caixa, não lançamentos da conta corrente. "
            "Envie o extrato de movimentações do investimento; datas de aplicação e vencimento não são movimentações bancárias.")
        setattr(error, "_parse_observability", {
            "layout_inference_name": "caixa_monthly_cdb_positions_v1", "layout_inference_confidence": .99,
            "selected_parser": "financial_report_unsupported_positions",
        })
        raise error
    if evidence == "btg_fund_positions_and_lots":
        from app.application.parsers.pdf.btg_fund_positions import parse_btg_fund_positions

        return parse_btg_fund_positions(page_texts)
    if evidence == "sicoob_rem116_period_summary":
        from app.application.parsers.pdf.sicoob_summary import parse_sicoob_summary

        return parse_sicoob_summary(page_texts)
    if evidence == "nubank_rdb_caixinhas_columns":
        from app.application.parsers.pdf.nubank_rdb import parse_nubank_rdb_pages

        return parse_nubank_rdb_pages(page_texts)
    lines = [(line.strip(), {"page": page, "line": number})
             for page, content in enumerate(page_texts, 1)
             for number, line in enumerate(content.splitlines(), 1) if line.strip()]
    for page in page_texts:
        other = detect_financial_report(page)
        if other is not None and other[0] != kind:
            raise PdfFinancialValidationError("Este PDF contém relatórios de tipos diferentes. Envie cada relatório separadamente.")
    events, observations, validations = {
        RECEIVABLES_REPORT: _receivables, INVESTMENT_STATEMENT: _investments, FISCAL_SUMMARY: _fiscal,
    }[kind](lines)
    if not events and not observations:
        raise PdfFinancialValidationError(MESSAGE)
    ofx_allowed = kind == RECEIVABLES_REPORT and all(v["status"] == "passed" for v in validations)
    descriptions = {
        RECEIVABLES_REPORT: (
            "Títulos recebidos: data de pagamento e valor cobrado. O documento não informa saldo bancário "
            "nem confirma a data de crédito na conta. O OFX representa recebimentos na conta de destino informada."
        ),
        INVESTMENT_STATEMENT: (
            "Movimentações de CDB: aplicações pelo principal e resgates pelo valor líquido. Posições e impostos "
            "estão preservados nos detalhes. A exportação OFX de investimentos ainda não está disponível."
        ),
        FISCAL_SUMMARY: (
            "Documentos fiscais emitidos: os valores não comprovam pagamentos ou recebimentos. "
            "Situação e direção fiscal estão preservadas nos detalhes; não há exportação bancária OFX."
        ),
    }
    return {
        "schema_version": "financial_events_v1", "document_type": kind, "evidence": [evidence],
        "origin": "GDOOR" if "GDOOR" in normalize_upper_text(text) else (
            "Bradesco" if "BRADESCO" in normalize_upper_text(text) else None),
        "representation": "text", "events": events, "observations": observations,
        "metadata": _document_metadata(lines, kind),
        "validations": validations, "notice": descriptions[kind],
        "export": {"available_formats": ["csv", "xlsx", "ofx"] if ofx_allowed else ["csv", "xlsx"],
                   "ofx_profile": "receipts" if ofx_allowed else None,
                   "requires_destination_account": ofx_allowed, "supports_row_edits": False,
                   "reason": descriptions[kind] if kind != RECEIVABLES_REPORT or ofx_allowed else
                       "OFX indisponível: os valores extraídos diferem dos totais impressos. Confira as validações no Excel."},
    }


def _document_metadata(lines, kind):
    metadata = {}
    text = " ".join(normalize_upper_text(line) for line, _ in lines)
    period = re.search(rf"PERIODO DE\s*:\s*({DATE})\s+ATE\s*:?\s*({DATE})", text)
    if period:
        metadata["period_start"], metadata["period_end"] = date(period[1]), date(period[2])
    for index, (line, _) in enumerate(lines[:20]):
        normalized = normalize_upper_text(line)
        if normalized.startswith("EMPRESA/CNPJ:"):
            owner, _, tax_id = line.split(":", 1)[1].partition("|")
            metadata.update(owner_name=owner.strip(), owner_tax_id=tax_id.strip() or None)
        elif normalized.startswith("AGENCIA/CONTA:"):
            branch, _, account = line.split(":", 1)[1].partition("|")
            metadata.update(source_bank_branch=branch.strip(), source_account_number=account.strip())
        elif "NOME DO CLIENTE" in normalized and index + 1 < len(lines):
            match = re.match(r"^\d+\s+(\d+)\s+(.+?)\s+\d{1,2}\s+de\s+", lines[index + 1][0], re.IGNORECASE)
            if match:
                metadata.update(product_account=match[1], owner_name=match[2])
    if kind == FISCAL_SUMMARY and len(lines) > 1 and "RELATORIO EMITIDO" in normalize_upper_text(lines[1][0]):
        metadata["owner_name"] = lines[0][0]
    return metadata


def _receivables(lines):
    events, totals = [], []
    pending, refs = [], []
    row_start = re.compile(r"^\d+\s+\S+/\d+\b")
    row = re.compile(rf"^\d+\s+(\S+/\d+)\s+(.+?)\s+({DATE})\s+({DATE})\s+({MONEY})\s+({MONEY})\s+(\S+)\s+({MONEY})$")
    for text, ref in lines:
        if normalize_upper_text(text).startswith("TOTAL "):
            if pending:
                raise PdfFinancialValidationError(MESSAGE)
            values = re.findall(MONEY, text)
            if len(values) == 3:
                totals.append(([money(v) for v in values], ref))
            continue
        if row_start.match(text):
            if pending:
                raise PdfFinancialValidationError(MESSAGE)
            pending, refs = [text], [ref]
        elif pending:
            # Repeated headers are not title descriptions.
            if normalize_upper_text(text).startswith(("TIPO SEU NUMERO", "TITULOS PAGOS", "PAGINA")):
                continue
            pending.append(text)
            refs.append(ref)
        else:
            if re.match(rf"^{DATE}\b", text) or (re.search(DATE, text) and len(re.findall(MONEY, text)) >= 2):
                raise PdfFinancialValidationError(MESSAGE)
            continue
        match = row.fullmatch(" ".join(pending))
        if match is None:
            continue
        reference, party, due, paid, original, collected, car, adjustment = match.groups()
        amounts = {"original": money(original), "collected": money(collected), "adjustment": money(adjustment)}
        events.append(_event("receipt", dates={"due": date(due), "payment": date(paid)}, amounts=amounts,
                             primary="collected", cash_effect=amounts["collected"], reference=reference,
                             description=f"Recebimento título {reference} — {party}", counterparty=party,
                             refs=refs, status="paid", attributes={"car": car}))
        pending, refs = [], []
    if pending or len(totals) != 1 or not events:
        raise PdfFinancialValidationError(MESSAGE)
    validations = [_check(f"titles_total_{field}", _sum(events, field), Decimal(expected), refs=[totals[0][1]])
                   for field, expected in zip(("original", "collected", "adjustment"), totals[0][0], strict=True)]
    for event in events:
        amounts = event["amounts"]
        validations.append(_check("title_nonnegative_collected", Decimal(amounts["collected"]) >= 0, True,
                                  refs=event["source_refs"]))
        validations.append(_check("title_adjustment", Decimal(amounts["collected"]) - Decimal(amounts["original"]),
                                  Decimal(amounts["adjustment"]), refs=event["source_refs"]))
    return events, [], validations


def _investments(lines):
    fields = ("principal", "gross", "income", "iof", "irrf", "net", "period_income")
    groups, current, totals = [], [], []
    header = " ".join(normalize_upper_text(text) for text, _ in lines)
    if not all(marker in header for marker in ("SALDO ANTERIOR", "APLICACOES", "RESGATES / VENCIMENTOS", "SALDO FINAL")):
        raise PdfFinancialValidationError(MESSAGE)
    row = re.compile(rf"^({DATE})\s+({DATE})\s+({DATE}|-)\s+(\d+,\d{{4}})\s+(.+)$")
    for text, ref in lines:
        if normalize_upper_text(text).startswith("TOTAL "):
            values = re.findall(MONEY, text)
            if len(values) != 7:
                raise PdfFinancialValidationError(MESSAGE)
            groups.append(current)
            totals.append(([money(v) for v in values], ref))
            current = []
        elif re.match(rf"^{DATE}\b", text):
            match = row.fullmatch(text)
            if match is None:
                raise PdfFinancialValidationError(MESSAGE)
            applied, maturity, redeemed, rate, tail = match.groups()
            values = re.findall(MONEY, tail)
            if " ".join(values) != tail or len(values) not in {4, 7}:
                raise PdfFinancialValidationError(MESSAGE)
            dates = {"application": date(applied), "maturity": date(maturity)}
            if redeemed != "-":
                dates["redemption"] = date(redeemed)
            current.append({"dates": dates, "amounts": dict(zip(fields, map(money, values), strict=True))
                            if len(values) == 7 else {"principal": money(values[0])},
                            "attributes": {"rate_percent": rate.replace(",", "."),
                                           "source_values": [money(value) for value in values]}, "ref": ref})
    if current or len(groups) != 4:
        raise PdfFinancialValidationError(MESSAGE)
    events, observations, validations = [], [], []
    for index, (group, (expected, total_ref)) in enumerate(zip(groups, totals, strict=True)):
        for field, printed in zip(fields, expected, strict=True):
            if all(field in item["amounts"] for item in group):
                validations.append(_check(f"investment_section_{index}_{field}", _sum(group, field), Decimal(printed), refs=[total_ref]))
        for item in group:
            amounts, dates, attrs, ref = item["amounts"], item["dates"], item["attributes"], item["ref"]
            if index in {0, 3}:
                if "redemption" in dates or len(amounts) != 7:
                    raise PdfFinancialValidationError(MESSAGE)
                observations.append({"observation_type": "opening_position" if index == 0 else "closing_position",
                                     "dates": dates, "amounts": amounts, "attributes": attrs, "source_refs": [ref]})
                continue
            is_redemption = index == 2
            if is_redemption != ("redemption" in dates) or (is_redemption and len(amounts) != 7):
                raise PdfFinancialValidationError(MESSAGE)
            cash = amounts["net"] if is_redemption else format(-Decimal(amounts["principal"]), ".2f")
            events.append(_event("investment_redemption" if is_redemption else "investment_application",
                                 dates=dates, amounts=amounts, primary="net" if is_redemption else "principal",
                                 cash_effect=cash, reference=None,
                                 description="Resgate CDB" if is_redemption else "Aplicação CDB", refs=[ref], attributes=attrs))
            if is_redemption:
                validations.append(_check("redemption_net", Decimal(amounts["gross"]) - Decimal(amounts["iof"]) - Decimal(amounts["irrf"]),
                                          Decimal(amounts["net"]), refs=[ref]))
    principal_end = _sum(groups[0], "principal") + _sum(groups[1], "principal") - _sum(groups[2], "principal")
    validations.append(_check("investment_principal_continuity", principal_end,
                              _sum(groups[3], "principal")))
    return events, observations, validations


def _fiscal(lines):
    events, validations = [], []
    row = re.compile(rf"^(\d+)\s+({DATE})\s+(?:(\d{{44}})\s+(\d+)\s+)?R\$\s*({MONEY})\s*([A-Z]*?)([ES])(\d{{2}})$")
    total = count = None
    direction_totals = {}
    total_ref = None
    for text, ref in lines:
        normalized = normalize_upper_text(text)
        if re.match(rf"^\d+\s+{DATE}", text):
            match = row.fullmatch(text)
            if match is None:
                raise PdfFinancialValidationError(MESSAGE)
            reference, issued, key, protocol, amount, status_code, direction, model = match.groups()
            value = money(amount)
            # Preserve the source code; I is an inutilizada number, not a receipt.
            status = {"I": "voided", "C": "cancelled", "": "issued"}.get(status_code, "unknown")
            attrs = {"access_key": key, "protocol": protocol, "model": model,
                     "status_code": status_code, "fiscal_direction": direction}
            events.append(_event("fiscal_document", dates={"issue": date(issued)}, amounts={"document": value},
                                 primary="document", cash_effect=None, reference=reference,
                                 description=f"Documento fiscal {reference} — modelo {model}", refs=[ref], status=status, attributes=attrs))
        elif re.match(rf"^{DATE}\b", text):
            raise PdfFinancialValidationError(MESSAGE)
        elif re.match(r"^TOTAL GERAL", normalized):
            values = re.findall(MONEY, text)
            if len(values) != 1 or total is not None:
                raise PdfFinancialValidationError(MESSAGE)
            total, total_ref = Decimal(money(values[0])), ref
        for label, direction in (("SAIDAS", "S"), ("ENTRADAS", "E")):
            if f"TOTAL {label}" in normalized:
                values = re.findall(MONEY, text)
                if len(values) != 1 or direction in direction_totals:
                    raise PdfFinancialValidationError(MESSAGE)
                direction_totals[direction] = Decimal(money(values[0]))
        match = re.search(r"(\d+)\s*REGISTROS", normalized)
        if match:
            if count is not None:
                raise PdfFinancialValidationError(MESSAGE)
            count = int(match.group(1))
    if not events or total is None or count is None:
        raise PdfFinancialValidationError(MESSAGE)
    validations.extend([_check("fiscal_record_count", len(events), count),
                        _check("fiscal_document_total", _sum(events, "document"), total, refs=[total_ref])])
    for direction in ("E", "S"):
        selected = [event for event in events if event["attributes"]["fiscal_direction"] == direction]
        validations.append(_check(f"fiscal_direction_{direction}", _sum(selected, "document"), direction_totals.get(direction)))
    return events, [], validations
