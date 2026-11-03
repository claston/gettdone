"""Signed movement sections with inherited dates and independent printed controls.

The same state machine consumes spatial native text and line-oriented text/OCR.
Statement headers, account positions and credit facilities are not movements.
"""
import re
from dataclasses import replace
from decimal import Decimal

from app.application.normalization.text import normalize_upper_text
from app.application.parsers.pdf.document_period import period_contains, unique_document_period
from app.application.parsers.pdf.layout_specific.shared import build_parsed_transaction
from app.application.parsers.pdf.statement_review import financial_mismatch
from app.application.parsers.pdf.verified_statements import DATE, NUMBER, VerifiedStatement, day, fail, money

SICREDI_MODERN = "sicredi_extrato_conta_corrente_moderno_movimentacoes_v1"
AMOUNT = re.compile(rf"(?P<sign>[+\-\u2212]?)\s*R\$\s*(?P<after_sign>[+\-\u2212]?)\s*(?P<value>{NUMBER})(?![\d,.%])")
DATED = re.compile(rf"^({DATE})(?:\s+|$)(.*)")


def is_sicredi_modern_statement(text):
    text = normalize_upper_text(text)
    return ("EXTRATO DE CONTA CORRENTE" in text and "MOVIMENTACOES" in text
            and "MOMENTO DE EMISSAO" in text and ("SICREDI" in text or "COOPERATIVA" in text))


def _amount(match):
    before, after = match["sign"].replace("\u2212", "-"), match["after_sign"].replace("\u2212", "-")
    if before and after and before != after:
        fail("Um valor possui sinais conflitantes; confira o documento original.")
    return money(match["value"]) * (-1 if (before or after) == "-" else 1)


class _MovementBlocks:
    def __init__(self):
        self.rows = []
        self.current_date = None
        self.current_date_ref = None
        self.waiting_date = False
        self.pending = []
        self.control = None
        self.controls = []
        self.inside = False
        self.header = False
        self.period_date = False
        self.unreadable_control = False

    def flush_description(self):
        if self.waiting_date or (self.pending and not self.rows):
            fail("Há um lançamento sem valor legível no extrato. Confira o documento original.")
        if self.pending:
            row = self.rows[-1]
            self.rows[-1] = replace(row, transaction=replace(row.transaction,
                description=" ".join([row.transaction.description, *(text for text, _ in self.pending)])),
                source_line_refs=(*row.source_line_refs, *(ref for _, ref in self.pending)))
            self.pending = []

    def read_control(self, text, ref):
        kind, date, parts = self.control
        if parts and not AMOUNT.fullmatch(text) and not (kind == "daily" and date is None and re.fullmatch(DATE, text)):
            self.control_unavailable()
            # A new transaction/date is not the missing value of a prior balance.
            if not re.match(r"^[+\-\u2212]?\s*R\$", text):
                self.read(text, *ref)
            return
        dates = re.findall(DATE, text)
        if dates:
            date = day(dates[0])
        parts = [*parts, ref]
        matches = list(AMOUNT.finditer(text))
        if len(matches) > 1:
            self.control_unavailable()
            return
        match = matches[0] if matches else None
        if match:
            self.controls.append((kind, date, _amount(match), parts))
            self.control = None
        else:
            if "R$" in text:
                self.control_unavailable()
                return
            self.control = kind, date, parts

    def control_unavailable(self):
        self.control = None
        self.unreadable_control = True
        financial_mismatch("Um saldo impresso não está legível; os movimentos foram preservados para revisão.")

    def read(self, raw, page, line):
        text = " ".join(raw.split())
        if not text:
            return
        normalized = normalize_upper_text(text)
        ref = (page, line)
        if normalized.startswith("MOVIMENTACOES"):
            if not self.waiting_date:
                self.flush_description()
            self.inside = self.header = True
            return
        if not self.inside:
            return
        if normalized.startswith("FIM DESSE EXTRATO") or re.match(r"^CHEQUE ESPECIAL(?:\s+EM\b|$)", normalized):
            self.flush_description()
            if self.control:
                self.control_unavailable()
            self.inside = False
            return
        if re.fullmatch(r"(?:PAGINA\s*)?\d+\s*(?:DE|/)\s*\d+", normalized):
            return
        if self.control:
            self.read_control(text, ref)
            return
        if normalized.startswith(("SALDO ANTERIOR", "SALDO DO DIA", "SALDO NO DIA")):
            self.flush_description()
            kind = "opening" if normalized.startswith("SALDO ANTERIOR") else (
                "closing" if normalized.startswith("SALDO NO DIA") else "daily")
            self.control = kind, None, []
            self.read_control(text, ref)
            self.header = False
            return
        if normalized in {"DATA", "DESCRICAO", "VALOR", "DATA DESCRICAO VALOR"}:
            if normalized in {"VALOR", "DATA DESCRICAO VALOR"}:
                self.header = False
            return
        if self.header and normalized in {"DE", "A"}:
            self.period_date = True
            return
        if self.header and self.period_date and re.fullmatch(DATE, text):
            day(text)
            self.period_date = False
            return
        if self.header and re.fullmatch(r"(?:=\s*)?(?:\d+\s*(?:DIAS?|MESES|MES))?", normalized):
            return
        self.header = False
        dated = DATED.match(text)
        if dated:
            self.flush_description()
            self.current_date = day(dated[1])
            self.current_date_ref = ref
            text = dated[2].strip()
            self.waiting_date = True
            if not text:
                return
        matches = list(AMOUNT.finditer(text))
        if "R$" in text and not matches:
            fail("Há um lançamento com valor ilegível no extrato.")
        if not matches:
            self.pending.append((text, ref))
            return
        if len(matches) != 1 or self.current_date is None:
            fail("Não foi possível associar a data e o valor de todos os lançamentos.")
        match = matches[0]
        description = (text[:match.start()] + text[match.end():]).strip(" -+")
        refs = [ref]
        if description and not self.waiting_date:
            self.flush_description()
        else:
            description = " ".join([*(part for part, _ in self.pending), description]).strip()
            refs = [*(source for _, source in self.pending), ref]
            self.pending = []
        if not description:
            fail("Um valor do extrato está sem descrição associável.")
        if self.current_date_ref not in refs:
            refs.append(self.current_date_ref)
        self.rows.append(build_parsed_transaction(date=self.current_date, description=description, amount=float(_amount(match)),
            source_page=refs[0][0], source_line=refs[0][1], has_explicit_amount_sign=True, source_line_refs=tuple(refs)))
        self.waiting_date = False

    def finish(self, layout):
        self.flush_description()
        if self.control:
            self.control_unavailable()
        openings = [value for kind, _, value, _ in self.controls if kind == "opening"]
        if len(set(openings)) > 1:
            fail("Há blocos independentes com saldos de abertura diferentes; confira os períodos.")
        opening = openings[0] if openings else None
        daily = [(date, value) for kind, date, value, _ in self.controls if kind == "daily"]
        if any(date is None for date, _ in daily):
            fail("Um saldo diário não possui data legível.")
        closings = [value for kind, _, value, _ in self.controls if kind == "closing"]
        if len(set(closings)) > 1:
            fail("O extrato possui controles finais incompatíveis.")
        closing = closings[-1] if closings else None
        if closing is None and daily and self.rows:
            last_date, last_value = max(daily)
            if last_date >= max(row.transaction.date for row in self.rows):
                closing = last_value
        checks = 0
        if opening is not None:
            for date, value in daily:
                subtotal = sum((Decimal(str(row.transaction.amount)) for row in self.rows if row.transaction.date <= date), Decimal(0))
                if opening + subtotal != value:
                    financial_mismatch()
                checks += 1
            if closing is not None:
                if opening + sum((Decimal(str(row.transaction.amount)) for row in self.rows), Decimal(0)) != closing:
                    financial_mismatch()
                checks += 1
        return VerifiedStatement(self.rows, layout, opening, closing, printed_balance_checks=checks,
            candidate_rows=len(self.rows), balance_checkpoints={},
            balance_bounds_unavailable=self.unreadable_control or opening is None or closing is None)


def parse_modern_movement_blocks(pages, *, layout=SICREDI_MODERN):
    periods = re.findall(rf"MOVIMENTACOES\s+DE\s+({DATE})\s+A\s+({DATE})", normalize_upper_text("\n".join(pages)))
    start, end = unique_document_period(periods)
    reader = _MovementBlocks()
    for page, text in enumerate(pages, 1):
        for line, raw in enumerate(text.splitlines(), 1):
            reader.read(raw, page, line)
    statement = reader.finish(layout)
    if any(not period_contains(row.transaction.date, start, end) for row in statement.rows):
        fail("Um lançamento está fora do período impresso do extrato.")
    return statement


def parse_modern_movement_lines(lines, *, layout=SICREDI_MODERN):
    reader = _MovementBlocks()
    for line in lines:
        reader.read(line.text, line.page_number, line.line_number)
    return reader.finish(layout)
