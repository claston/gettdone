"""Typed Nubank RDB events: net cash flows, taxes and non-cash investment positions."""
import re
from datetime import datetime
from decimal import Decimal

from app.application.errors import PdfFinancialValidationError
from app.application.normalization.text import normalize_upper_text
from app.application.parsers.pdf.document_period import period_contains, unique_document_period

_NUMBER = r"(?:\d{1,3}(?:\.\d{3})+|\d+),\d{2}"
_MONEY = re.compile(rf"R\$\s*({_NUMBER})")
_DATE = re.compile(r"^(\d{2}/\d{2}/\d{4})\s+(.*)$")
_PERIOD = re.compile(r"PERIODO:\s*(\d{2}) ([A-Z]{3}) (\d{4}) A (\d{2}) ([A-Z]{3}) (\d{4})")
_MONTHS = ("JAN", "FEV", "MAR", "ABR", "MAI", "JUN", "JUL", "AGO", "SET", "OUT", "NOV", "DEZ")
_MESSAGE = "Não foi possível conferir todas as operações, os impostos e as páginas do extrato Nubank Caixinhas PJ."


def _fail():
    raise PdfFinancialValidationError(_MESSAGE)


def _money(raw):
    return Decimal(raw.replace(".", "").replace(",", "."))


def _period(match):
    try:
        return tuple(datetime(int(match[offset + 2]), _MONTHS.index(match[offset + 1]) + 1,
                              int(match[offset])).date().isoformat() for offset in (1, 4))
    except (ValueError, IndexError) as exc:
        raise PdfFinancialValidationError(_MESSAGE) from exc


def parse_nubank_rdb_pages(pages: list[str]) -> dict:
    from app.application.parsers.pdf.financial_reports import _check, _event, date

    periods, balances, position_refs = set(), set(), []
    normalized_document = normalize_upper_text(" ".join(pages))
    if "NU FINANCEIRA" not in normalized_document or "RDB" not in normalized_document:
        _fail()
    for number, page in enumerate(pages, 1):
        normalized = normalize_upper_text(page)
        match = _PERIOD.search(normalized)
        if match is not None:
            periods.add(_period(match))
        for match in re.finditer(rf"SALDO NO FINAL DO PERIODO:\s*R\$\s*({_NUMBER})", normalized):
            balances.add(_money(match[1]))
        position_refs.extend({"page": number, "line": index} for index, text in enumerate(page.splitlines(), 1)
                             if "SALDO NO FINAL DO PERIODO:" in normalize_upper_text(text))
    if len(balances) != 1:
        _fail()
    start, end = unique_document_period(periods, parse_date=lambda value: value)
    events, observations, validations = [], [], []
    pending = []
    processing = False
    no_pending = False

    def flush():
        nonlocal pending
        if not pending:
            return
        text = normalize_upper_text(" ".join(line for line, _ in pending))
        match = _DATE.fullmatch(text)
        if match is None:
            _fail()
        day = date(match[1])
        if not period_contains(day, start, end):
            _fail()
        values = [_money(match[1]) for match in _MONEY.finditer(text)]
        description = normalize_upper_text(_MONEY.sub("", match[2]))
        refs = [ref for _, ref in pending]
        if description == "RENDIMENTO ATE ESSA DATA" and len(values) == 1:
            if end is not None and day != end or observations:
                _fail()
            observations.append({"observation_type": "period_income", "dates": {"as_of": day},
                                 "amounts": {"income": format(values[0], ".2f")}, "source_refs": refs, "attributes": {}})
        elif description in {"COMPRA POR APLICACAO", "RESGATE"} and len(values) == 4:
            gross, irrf, iof, net = values
            if gross - irrf - iof != net:
                _fail()
            application = description == "COMPRA POR APLICACAO"
            if application and (irrf or iof):
                _fail()
            amounts = dict(zip(("gross", "irrf", "iof", "net"), (format(value, ".2f") for value in values), strict=True))
            events.append(_event("investment_application" if application else "investment_redemption",
                dates={"application" if application else "redemption": day}, amounts=amounts, primary="net",
                cash_effect=format(-net if application else net, ".2f"), reference=None,
                description="Aplicação RDB Caixinhas PJ" if application else "Resgate RDB Caixinhas PJ", refs=refs))
            validations.append(_check("rdb_net_after_taxes", gross - irrf - iof, net, refs=refs))
        else:
            _fail()
        pending = []

    for page_number, page in enumerate(pages, 1):
        in_table = False
        for line_number, raw in enumerate(page.splitlines(), 1):
            text = normalize_upper_text(raw)
            if not text:
                continue
            ref = {"page": page_number, "line": line_number}
            if text.startswith("DATA MOVIMENTACAO"):
                in_table = True
            elif text.startswith("TRANSACOES EM PROCESSAMENTO"):
                flush()
                processing = True
            elif "SEM TRANSACOES EM PROCESSAMENTO NO PERIODO" in text:
                no_pending = True
            elif _DATE.match(text):
                if processing:
                    _fail()
                flush()
                pending = [(raw, ref)]
            elif pending:
                if text in {"APLICACAO", "ESSA DATA"} or _MONEY.search(text):
                    pending.append((raw, ref))
                else:
                    flush()
            elif in_table and _MONEY.search(text):
                _fail()  # A monetary row with no date must never disappear silently.
        flush()
    if not events or not no_pending or len(observations) != 1:
        _fail()
    observations.append({"observation_type": "closing_position", "dates": {"as_of": end} if end else {},
                         "amounts": {"net": format(next(iter(balances)), ".2f")}, "source_refs": position_refs, "attributes": {}})
    validations.append(_check("rdb_page_count", len(pages), len(pages)))
    notice = ("Aplicações e resgates RDB das Caixinhas PJ, com valores brutos, impostos e líquidos preservados. "
              "Posições e rendimentos não são saldos bancários. A exportação OFX de investimentos ainda não está disponível.")
    return {
        "schema_version": "financial_events_v1", "document_type": "investment_statement", "origin": "Nubank",
        "evidence": ["nubank_rdb_caixinhas_columns"], "events": events, "observations": observations,
        "metadata": {"period_start": start, "period_end": end, "product": "RDB Caixinhas PJ"},
        "validations": validations, "notice": notice,
        "export": {"available_formats": ["csv", "xlsx"], "ofx_profile": None,
                   "requires_destination_account": False, "supports_row_edits": False, "reason": notice},
    }
