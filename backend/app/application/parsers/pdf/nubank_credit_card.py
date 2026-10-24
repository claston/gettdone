"""Nubank invoices: dated movements, excluding holder and payment subtotals."""

import re
from datetime import date
from decimal import Decimal

from app.application.errors import PdfFinancialValidationError
from app.application.normalization.text import normalize_upper_text
from app.application.parsers.pdf.layout_specific.shared import build_parsed_transaction
from app.application.parsers.pdf.statement_review import financial_mismatch

LAYOUT = "nubank_fatura_cartao_credito_v1"
_MONTHS = {name: i for i, name in enumerate(("JAN", "FEV", "MAR", "ABR", "MAI", "JUN", "JUL", "AGO", "SET", "OUT", "NOV", "DEZ"), 1)}
_MONTH = "(?:" + "|".join(_MONTHS) + ")"
_DATE = re.compile(rf"^(\d{{2}})\s+({_MONTH})(?:\s+|$)(.*)$")
_AMOUNT = re.compile(r"([+−-]?)\s*R\$\s*([+−-]?)\s*((?:\d{1,3}(?:\.\d{3})+|\d+),\d{2})\s*$")


def is_nubank_invoice(text: str) -> bool:
    normalized = normalize_upper_text(text)
    return ("NUBANK" in normalized and "FATURA" in normalized
            and "TRANSACOES DE" in normalized
            and ("RESUMO DA FATURA ATUAL" in normalized or "TOTAL DE COMPRAS DE TODOS OS CARTOES" in normalized))


def _amount(text):
    match = _AMOUNT.search(text)
    if match is None:
        return None
    value = Decimal(match[3].replace(".", "").replace(",", "."))
    return text[:match.start()].strip(), -value if any(s in {"-", "−"} for s in match.group(1, 2)) else value


def _one(values):
    if not values or len(set(values)) != 1:
        raise PdfFinancialValidationError("Não foi possível identificar os totais da fatura Nubank. Confira o PDF original.")
    return values[0]


def parse_nubank_invoice(pages):
    # Imported lazily because the verified-statement dispatcher imports this module.
    from app.application.parsers.pdf.verified_statements import VerifiedStatement

    text = "\n".join(pages)
    due_dates = [match.groups() for raw in text.splitlines()
                 if (match := re.match(rf"^FATURA\s+(\d{{2}})\s+({_MONTH})\s+(\d{{4}})\b", normalize_upper_text(raw)))]
    try:
        due = _one([date(int(year), _MONTHS[month], int(day)) for day, month, year in due_dates])
    except ValueError as exc:
        raise PdfFinancialValidationError("A fatura Nubank contém uma data inválida.") from exc
    controls = {key: [] for key in ("previous", "payments", "purchases", "other", "closing")}
    labels = {"FATURA ANTERIOR": "previous", "PAGAMENTO RECEBIDO": "payments",
              "OUTROS LANCAMENTOS": "other", "TOTAL A PAGAR": "closing"}
    summary = False
    for line in text.splitlines():
        normalized = normalize_upper_text(line)
        if normalized == "RESUMO DA FATURA ATUAL":
            summary = True
            continue
        if summary and normalized.startswith(("PAGAMENTO MINIMO", "PROXIMAS FATURAS", "TRANSACOES DE")):
            summary = False
        parsed = _amount(line)
        if not summary or parsed is None:
            continue
        label, amount = parsed
        label = normalize_upper_text(label)
        key = "purchases" if label.startswith("TOTAL DE COMPRAS DE TODOS OS CARTOES") else labels.get(label)
        if key:
            controls[key].append(amount)
    previous, payment_total, purchases, closing = [_one(controls[key]) for key in ("previous", "payments", "purchases", "closing")]
    other = _one(controls["other"]) if controls["other"] else Decimal(0)
    rows, source_amounts, payment_amounts = [], [], []
    pending_date, pending_description, pending_source = None, [], None
    section = False
    payment_subtotals = []
    for page_number, page in enumerate(pages, 1):
        section = False
        for line_number, raw in enumerate(page.splitlines(), 1):
            raw = raw.strip()
            normalized = normalize_upper_text(raw)
            if normalized.startswith("TRANSACOES DE"):
                section = True
                continue
            if not section or not raw or re.fullmatch(r"\d+\s+DE\s+\d+", normalized):
                continue
            if normalized.startswith("EM CUMPRIMENTO"):
                section = False
                continue
            parsed = _amount(raw)
            if parsed and normalize_upper_text(parsed[0]) == "PAGAMENTOS":
                if pending_date is not None:
                    raise PdfFinancialValidationError("Há um lançamento sem valor na fatura Nubank.")
                payment_subtotals.append(parsed[1])
                continue
            dated = _DATE.match(normalized)
            if dated:
                if pending_date is not None:
                    raise PdfFinancialValidationError("Há um lançamento sem valor na fatura Nubank.")
                day, month = int(dated[1]), _MONTHS[dated[2]]
                try:
                    pending_date = date(due.year - int((month, day) > (due.month, due.day)), month, day).isoformat()
                except ValueError as exc:
                    raise PdfFinancialValidationError("A fatura Nubank contém uma data inválida.") from exc
                pending_source = (page_number, line_number)
                raw = re.sub(rf"^\d{{2}}\s+{_MONTH}\s*", "", raw, flags=re.IGNORECASE).strip()
                parsed = _amount(raw)
            if pending_date is None:
                # Holder totals and repeated page headers are never transactions.
                continue
            if parsed is None:
                if raw:
                    pending_description.append(raw)
                continue
            description, amount = parsed
            description = " ".join([*pending_description, description]).strip()
            if not description:
                raise PdfFinancialValidationError("Há um lançamento sem descrição na fatura Nubank.")
            rows.append(build_parsed_transaction(date=pending_date, description=description, amount=float(-amount),
                source_page=pending_source[0], source_line=pending_source[1], has_explicit_amount_sign=True))
            source_amounts.append(amount)
            if normalize_upper_text(description).startswith("PAGAMENTO EM "):
                payment_amounts.append(amount)
            pending_date, pending_description, pending_source = None, [], None
    if pending_date is not None or not rows:
        raise PdfFinancialValidationError("Não foi possível ler todos os lançamentos da fatura Nubank.")
    payments = sum(payment_amounts, Decimal(0))
    non_payments = sum(source_amounts, Decimal(0)) - payments
    if (payments != payment_total or non_payments != purchases + other
            or previous + payment_total + purchases + other != closing
            or any(total != payments for total in payment_subtotals)):
        financial_mismatch("Os pagamentos ou as compras não conferem com o resumo da fatura Nubank.")
    return VerifiedStatement(rows, LAYOUT, -previous, -closing, printed_balance_checks=4, candidate_rows=len(rows))
