"""Explicit statement variants with printed ledger controls, before generic parsing."""

import re
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal

from app.application.errors import PdfFinancialValidationError
from app.application.normalization.text import normalize_upper_text
from app.application.parsers.pdf.layout_specific.shared import build_parsed_transaction
from app.application.parsers.pdf.statement_review import financial_mismatch

NUMBER = r"(?:\d{1,3}(?:\.\d{3})+|\d+),\d{2}"
MONEY = re.compile(rf"(?<![\w.,])({NUMBER})(-?)(?![\d,.])")
DATE = r"\d{2}/\d{2}/\d{4}"


@dataclass
class VerifiedStatement:
    rows: list
    layout: str
    opening: Decimal | None
    closing: Decimal | None
    partial: bool = False
    printed_balance_checks: int = 0
    candidate_rows: int = 0
    opening_inferred: bool = False
    non_cash_entries: int = 0
    balance_checkpoints: dict[int, float] | None = None
    scheduled_entries_excluded: int = 0
    opening_ambiguity_reason: str = ""
    balance_bounds_unavailable: bool = False


def fail(message="Não foi possível conferir todos os lançamentos e saldos impressos. Envie o extrato original completo."):
    raise PdfFinancialValidationError(message)


def money(value):
    return Decimal(value.replace(".", "").replace(",", "."))


def day(value, pattern="%d/%m/%Y"):
    try:
        return datetime.strptime(value, pattern).date().isoformat()
    except ValueError:
        fail("O extrato contém uma data inválida. Confira o documento original.")


def identify_verified_statement(text):
    from app.application.parsers.pdf.nubank_credit_card import LAYOUT as NUBANK_CARD_LAYOUT
    from app.application.parsers.pdf.nubank_credit_card import is_nubank_invoice

    if is_nubank_invoice(text):
        return NUBANK_CARD_LAYOUT
    from app.application.parsers.pdf.uniprime_ocr import LAYOUT as UNIPRIME_LAYOUT
    from app.application.parsers.pdf.uniprime_ocr import is_uniprime_statement

    if is_uniprime_statement(text):
        return UNIPRIME_LAYOUT
    from app.application.parsers.pdf.final_native_statements import identify_final_native

    final = identify_final_native(text)
    if final:
        return final
    from app.application.sweep_statement_detection import identify_sweep_statement

    sweep = identify_sweep_statement(text)
    if sweep:
        return sweep
    from app.application.structured_statement_detection import identify_structured_statement

    structured = identify_structured_statement(text)
    if structured:
        return structured
    from app.application.parsers.pdf.afternoon_statements import identify_afternoon_statement

    afternoon = identify_afternoon_statement(text)
    if afternoon:
        return afternoon
    from app.application.parsers.pdf.signed_statement_variants import identify_variant

    variant = identify_variant(text)
    if variant:
        return variant
    normalized = normalize_upper_text(text)
    if "BVP-PORTAL BANRISUL" in normalized and "MOVIMENTOS DA CONTA CORRENTE" in normalized:
        return "banrisul_portal_monthly_v1"
    if ("RELATORIO DE CONTA CORRENTE" in normalized and "SANTANDER" in normalized
            and "DEBITO" in normalized and "CREDITO" in normalized):
        return "santander_mister_contador_signed_report_v1"
    if "BANCO XP S.A" in normalized and "SALDO DISPONIVEL NO FINAL DO PERIODO FILTRADO" in normalized:
        return "xp_digital_descending_v1"
    if (
        "EXTRATODECONTACORRENTE" in normalized.replace(" ", "")
        and "DIALOTEDOCUMENTOHISTORICOVALOR" in normalized.replace(" ", "")
        and re.search(rf"{NUMBER}\s*\([+-]\)", text)
    ):
        return "banco_do_brasil_extrato_conta_corrente_lancamentos_v1"
    if "BANRISUL" in normalized.replace(" ", "") and "DIA OP DOC SIS HISTORICO" in normalized:
        return "banrisul_extrato_op_doc_sis_v1"
    if "FINANCEHUB" in normalized and "SALDO INICIAL DO PERIODO" in normalized and "SALDO FINAL" in normalized:
        return "financehub_statement_v1"
    return None


def parse_verified_statement(pages):
    text = "\n".join(pages)
    kind = identify_verified_statement(text)
    if kind == "nubank_fatura_cartao_credito_v1":
        from app.application.parsers.pdf.nubank_credit_card import parse_nubank_invoice

        return parse_nubank_invoice(pages)
    if kind == "uniprime_conta_corrente_ocr_v1":
        fail("Este modelo Uniprime exige as coordenadas da tabela para conferir valores e saldos. Envie o PDF original para revisão.")
    from app.application.parsers.pdf.final_native_statements import identify_final_native, parse_final_native

    if kind and kind == identify_final_native(text):
        return parse_final_native(pages, kind)
    from app.application.sweep_statement_detection import identify_sweep_statement

    if kind == "banco_inter_extrato_conta_corrente_saldo_transacao_v1":
        return None
    if kind and kind == identify_sweep_statement(text):
        from app.application.parsers.pdf.sweep_native_statements import parse_sweep_statement

        return parse_sweep_statement(pages, kind)
    from app.application.structured_statement_detection import identify_structured_statement

    if kind and kind == identify_structured_statement(text):
        from app.application.parsers.pdf.structured_statement_variants import parse_structured_statement

        return parse_structured_statement(pages, kind)
    if kind and kind.startswith("recovery_"):
        from app.application.parsers.pdf.afternoon_statements import parse_afternoon_statement

        return parse_afternoon_statement(pages, kind)
    from app.application.parsers.pdf.signed_statement_variants import identify_variant, parse_variant

    if kind and kind == identify_variant(text):
        return parse_variant(pages, kind)
    if kind in {"banrisul_portal_monthly_v1", "santander_mister_contador_signed_report_v1"}:
        from app.application.parsers.pdf.followup_native import parse_followup_native

        return parse_followup_native(pages, kind)
    if kind is None:
        # These exports discard the debit/credit columns and bank identity. A
        # description alone cannot determine every direction (e.g. transfers).
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        grouped = sum(
            bool(
                re.fullmatch(r"\d{2}/\d{2}/\d{2}", lines[i])
                and re.fullmatch(r"\d{6,10}", lines[i + 1])
                and re.fullmatch(NUMBER, lines[i + 3])
            )
            for i in range(len(lines) - 3)
        )
        inline = sum(bool(re.fullmatch(rf"\d{{2}}/\d{{2}}/\d{{2}}\s+\d{{6,10}}\s+.+\s+{NUMBER}", line)) for line in lines)
        if "INVEST FACIL" in normalize_upper_text(text) and max(grouped, inline) >= 2:
            fail("O arquivo não informa o sinal nem as colunas de débito e crédito. Envie o extrato original com essas informações.")
        return None
    return {
        "xp_digital_descending_v1": _xp,
        "banco_do_brasil_extrato_conta_corrente_lancamentos_v1": _bb,
        "banrisul_extrato_op_doc_sis_v1": _banrisul,
        "financehub_statement_v1": _financehub,
    }[kind](pages, kind)


def _lines(pages):
    return [
        (page, number, line.strip())
        for page, content in enumerate(pages, 1)
        for number, line in enumerate(content.splitlines(), 1)
        if line.strip()
    ]


def _row(page, line, date, description, amount, balance=None, reference=None):
    return build_parsed_transaction(
        date=date,
        description=description,
        amount=float(amount),
        running_balance=float(balance) if balance is not None else None,
        external_reference_id=reference,
        source_page=page,
        source_line=line,
        has_explicit_amount_sign=True,
        source_line_refs=((page, line),),
    )


def _bb(pages, kind):
    rows, opening, running, closing = [], None, None, None
    blocked = 0
    pending_label = ""
    period = re.search(r"PERIODO:\s*(\d{2})\s+A\s+(\d{2}/\d{2}/\d{4})", normalize_upper_text("\n".join(pages)))
    if period is None:
        from app.application.parsers.pdf.signed_statement_variants import parse_bb_split

        return parse_bb_split(pages, kind)
    end = day(period[2])
    start = day(period[1] + period[2][2:])
    for page, line, raw in _lines(pages):
        if closing is not None and normalize_upper_text(raw).startswith((
            "INFORMACOES ADICIONAIS", "LANCAMENTOS FUTUROS", "APLICACOES FINANCEIRAS",
        )):
            # Account positions and future placeholders follow the final cash
            # balance. They are not part of the validated historical ledger.
            break
        if ("DEPOSITO BLOQUEAD." in normalize_upper_text(raw)
                or re.search(r"\bDEPOS CHQ IMAGEM \d+ DIAS?\b", normalize_upper_text(raw))) and re.search(rf"{NUMBER}\s*\*$", raw):
            blocked += 1
            continue
        match = re.match(rf"^({DATE})\s+(.+?)\s+({NUMBER})\s*\(([+-])\)$", raw)
        if not match:
            if re.match(rf"^{DATE}\b", raw):
                fail()
            label = normalize_upper_text(raw)
            if re.fullmatch(r"PIX\s*-\s*ENVIADO|PAGAMENTO DE BOLETO|PAGAMENTO DE IMPOSTOS", label):
                pending_label = raw
            elif (
                rows
                and rows[-1].running_balance is None
                and closing is None
                and not label.startswith(("EXTRATO", "CLIENTE:", "PERIODO:", "DIA ", "LANCAMENTOS"))
            ):
                previous = rows[-1]
                rows[-1] = replace(
                    previous,
                    transaction=replace(previous.transaction, description=previous.transaction.description + " " + raw),
                    source_line_refs=(*previous.source_line_refs, (page, line)),
                )
            continue
        date, description, value, sign = match.groups()
        amount = money(value) * (-1 if sign == "-" else 1)
        date = day(date)
        normalized = normalize_upper_text(description)
        compact = re.sub(r"\s", "", normalized)
        if "SALDOANTERIOR" in compact:
            if opening is not None or rows:
                fail()
            if date > start:
                financial_mismatch()
            opening = running = amount
        elif "SALDODODIA" in compact or compact == "SALDO":
            if not start <= date <= end:
                financial_mismatch()
            if amount != running:
                financial_mismatch()
            if rows:
                rows[-1] = replace(rows[-1], running_balance=float(amount), source_line_refs=(*rows[-1].source_line_refs, (page, line)))
            if compact == "SALDO":
                closing = amount
        else:
            if running is None or closing is not None or rows and date < rows[-1].transaction.date:
                fail()
            if not start <= date <= end:
                financial_mismatch()
            running += amount
            columns = re.match(r"^(\d{5})\s+(\d+)\s+(.+)$", description)
            reference = columns[2] if columns else None
            description = columns[3] if columns else description
            if pending_label:
                description = pending_label + " " + description
                pending_label = ""
            rows.append(_row(page, line, date, description, amount, reference=reference))
    if not rows or closing is None:
        fail()
    return VerifiedStatement(rows, kind, opening, closing, non_cash_entries=blocked)


def _xp(pages, kind):
    text = "\n".join(pages)
    period = re.search(rf"DE:\s*({DATE})\s+ATE:\s*({DATE})", normalize_upper_text(text))
    printed = re.search(rf"SALDO DISPONIVEL NO FINAL DO PERIODO FILTRADO:\s*R\$\s*({NUMBER})", normalize_upper_text(text))
    if not period or not printed:
        fail()
    start, end = day(period[1]), day(period[2])
    rows = []
    in_footer = False
    currency = re.compile(rf"(-?)R\$\s*({NUMBER})")
    for page, line, raw in _lines(pages):
        match = re.match(r"^(\d{2}/\d{2}/\d{2})\s+às\s+(\d{1,2}:\s*\d{1,2}:\s*\d{1,2})\s+(.+)$", raw, re.I)
        if not match:
            if re.match(r"^\d{2}/\d{2}/\d{2}\b", raw):
                fail()
            if normalize_upper_text(raw).startswith(("EXTRATO SIMPLES", "IMPORTANTE:")):
                in_footer = True
            if rows and not in_footer and rows[-1].source_page == page:
                previous = rows[-1]
                rows[-1] = replace(
                    previous,
                    transaction=replace(previous.transaction, description=previous.transaction.description + " " + raw),
                    source_line_refs=(*previous.source_line_refs, (page, line)),
                )
            continue
        values = list(currency.finditer(match[3]))
        if len(values) != 2 or match[3][values[-1].end() :].strip():
            fail()
        amount = money(values[0][2]) * (-1 if values[0][1] else 1)
        balance = money(values[1][2]) * (-1 if values[1][1] else 1)
        date = day(match[1], "%d/%m/%y")
        if not start <= date <= end:
            fail()
        rows.append(_row(page, line, date, match[3][: values[0].start()].strip(), amount, balance))
    if not rows or any(a.transaction.date < b.transaction.date for a, b in zip(rows, rows[1:])):
        fail()
    rows.reverse()  # Reverse the complete physical order, including same-day times.
    opening = Decimal(str(rows[0].running_balance)) - Decimal(str(rows[0].transaction.amount))
    running = opening
    for row in rows:
        running += Decimal(str(row.transaction.amount))
        if running != Decimal(str(row.running_balance)):
            financial_mismatch()
    if running != money(printed[1]):
        financial_mismatch()
    return VerifiedStatement(rows, kind, opening, running)


def _banrisul(pages, kind):
    months = {
        name: i
        for i, name in enumerate(
            ("JANEIRO", "FEVEREIRO", "MARCO", "ABRIL", "MAIO", "JUNHO", "JULHO", "AGOSTO", "SETEMBRO", "OUTUBRO", "NOVEMBRO", "DEZEMBRO"), 1
        )
    }
    text = normalize_upper_text("\n".join(pages))
    header = re.search(r"BANRISUL\s*-?\s*([A-Z]+)\s*/(\d{4})", text.replace("B A N R I S U L", "BANRISUL"))
    if not header or header[1] not in months:
        fail()
    month, year = months[header[1]], int(header[2])
    rows = []
    current_day = None
    opening_match = re.search(rf"SALDO ANTERIOR EM\s+({DATE})\s+({NUMBER})(-?)", text)
    if not opening_match or "FIM DE EXTRATO" not in text:
        fail()
    opening = money(opening_match[2]) * (-1 if opening_match[3] else 1)
    running = opening
    checked = 0
    for page, line, raw in _lines(pages):
        match = re.match(rf"^(?:(\d{{2}})\s+)?(\d{{4}})\s+([A-Z0-9]{{6}})\s+([A-Z]{{3}})\s+(.+?)\s+({NUMBER})(-?)(?:\s+[SR*]+)?$", raw)
        if match:
            if match[1]:
                current_day = int(match[1])
            if current_day is None:
                fail()
            try:
                date = datetime(year, month, current_day).date().isoformat()
            except ValueError:
                fail()
            amount = money(match[6]) * (-1 if match[7] else 1)
            running += amount
            rows.append(_row(page, line, date, match[5], amount, reference=match[3]))
        elif "SALDO NA DATA" in normalize_upper_text(raw):
            control = re.search(rf"SALDO NA DATA\s+({NUMBER})(-?)", normalize_upper_text(raw))
            if not control or running != money(control[1]) * (-1 if control[2] else 1) or not rows:
                fail()
            rows[-1] = replace(rows[-1], running_balance=float(running))
            checked += 1
        elif re.match(r"^(?:\d{2}\s+)?\d{4}\s+[A-Z0-9]{6}\s+[A-Z]{3}\b", raw):
            fail()
    if not rows or not checked or rows[-1].running_balance is None:
        fail()
    return VerifiedStatement(rows, kind, opening, running)


def _financehub(pages, kind):
    text = normalize_upper_text("\n".join(pages))
    bounds = re.search(rf"SALDO INICIAL DO PERIODO:\s*R\$\s*({NUMBER}).*?SALDO FINAL\s*\([^)]*\):\s*R\$\s*({NUMBER})", text)
    values = re.findall(rf"R\$\s*({NUMBER})", text.split("LANCAMENTOS")[0])
    if not bounds or len(values) != 5:
        fail()
    rows = []
    for page, line, raw in _lines(pages):
        if re.match(rf"^{DATE}\b", raw):
            match = re.fullmatch(rf"({DATE})\s+(.+?)\s+([+-])\s*R\$\s*({NUMBER})", raw)
            if not match:
                fail()
            rows.append(_row(page, line, day(match[1]), match[2], money(match[4]) * (-1 if match[3] == "-" else 1)))
    incoming = sum((Decimal(str(r.transaction.amount)) for r in rows if r.transaction.amount > 0), Decimal(0))
    outgoing = -sum((Decimal(str(r.transaction.amount)) for r in rows if r.transaction.amount < 0), Decimal(0))
    opening, closing = money(bounds[1]), money(bounds[2])
    if (
        not rows
        or (incoming, outgoing) != (money(values[0]), money(values[1]))
        or incoming - outgoing != money(values[2])
        or opening + incoming - outgoing != closing
    ):
        fail()
    return VerifiedStatement(rows, kind, opening, closing)
