"""Shared geometry and provenance for monthly positions and payment reports."""
import re
from dataclasses import dataclass, replace
from decimal import Decimal
from statistics import median

from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.financial_reports import _check
from app.application.parsers.pdf.verified_statements import fail

EVIDENCES = {'sicredi_term_deposit_monthly', 'bb_monthly_fund_positions', 'stone_payment_receipt_rows'}
NUMBER = r'[+-]?(?:\d{1,3}(?:\.\d{3})+|\d+),\d+'
MESSAGE = 'Há um campo financeiro sem associação inequívoca neste relatório. Confira o documento original.'


def decimal(text, *, places=2):
    text = re.sub(r'\s+', '', text).removeprefix('R$')
    if not re.fullmatch(NUMBER, text) or (places is not None and len(text.rsplit(',', 1)[-1]) != places):
        fail(MESSAGE)
    return Decimal(text.replace('.', '').replace(',', '.'))


def safe_text(text):
    # Unmapped glyphs are preserved explicitly, not silently changed to a guessed
    # reference character; this also keeps XML exports free of control bytes.
    return ''.join(f'\\u{ord(c):04x}' if ord(c) < 32 or 0xE000 <= ord(c) <= 0xF8FF else c for c in text)


@dataclass
class Row:
    page: object
    words: list

    @property
    def text(self):
        return ' '.join(w.text for w in self.words)

    @property
    def normalized(self):
        return upper(self.text)

    @property
    def refs(self):
        ids = {w.id for w in self.words}
        return [{'page': self.page.page_number, 'line': line.line_index}
                for line in self.page.lines if ids.intersection(line.word_ids)]


def visual_rows(source):
    rows = []
    for page in source.pages:
        if any(w.text.strip() and not w.bbox for w in page.words) or any(
                line.text.strip() and not line.word_ids for line in page.lines):
            fail('Uma linha do relatório está sem coordenadas de origem; a cobertura não pode ser confirmada.')
        words = [w for w in page.words if w.bbox]
        if not words:
            fail('Uma página do relatório está sem evidência legível; a extração não pode ser apresentada como completa.')
        tolerance = median(w.bbox['height'] for w in words)*.45
        groups = []
        for word in sorted(words, key=lambda w: w.bbox['top']+w.bbox['height']/2):
            y = word.bbox['top']+word.bbox['height']/2
            if not groups or abs(groups[-1][0]-y) > tolerance:
                groups.append((y, [word]))
            else:
                # Only coincident OCR ink may be folded; equal financial rows
                # at different positions/pages always remain separate records.
                duplicate = any(w.text == word.text and all(abs(w.bbox[k]-word.bbox[k]) < .0001
                                for k in ('left', 'top', 'width', 'height')) for w in groups[-1][1])
                if not duplicate:
                    groups[-1][1].append(word)
        rows.extend(Row(page, sorted(group, key=lambda w: w.bbox['left'])) for _, group in groups)
    return rows


def refs(rows):
    return list({(ref['page'], ref['line']): ref for row in rows for ref in row.refs}.values())


def observation(kind, row, *, amounts=None, dates=None, attributes=None):
    return {'observation_type': kind, 'dates': dates or {}, 'amounts': amounts or {},
            'attributes': {'label': safe_text(row.text), **(attributes or {}), 'source_fields': {'Linha impressa': safe_text(row.text)}},
            'source_refs': row.refs}


def account(rows):
    text = '\n'.join(r.normalized for r in rows)
    matches = re.findall(r'\bCONTA(?: CORRENTE)?[.: ]*([\d.-]+)', text)
    # The consolidated Sicredi PDF prints labels and values in opposite text
    # object order; geometry, rather than stream order, supplies this context.
    values = set(matches)
    if len(values) > 1:
        fail('Há contas diferentes dentro de um mesmo bloco de relatório.')
    return next(iter(values)) if values else None


def blocks(rows, title, complete):
    result, current = [], []
    for row in rows:
        if title(row.normalized) and current and complete(current):
            # Include page metadata printed above the title in the new block.
            boundary = next((i for i, r in enumerate(current) if r.page.page_number == row.page.page_number), len(current))
            if boundary == 0:
                boundary = len(current)  # another complete report on the same physical page
            previous, current = current[:boundary], current[boundary:]
            if previous:
                result.append(previous)
        current.append(row)
    if current:
        result.append(current)
    return result


def document(origin, evidence, observations, checks, metadata, *, events=None):
    notice = ('Relatório de pagamentos Stone: modalidade, bandeira, situação e identificadores preservados. '
              'Os repasses informados não são lançamentos de conta corrente. CSV e Excel disponíveis; OFX indisponível.'
              if origin == 'Stone' else
              'Relatório de investimentos: posições, movimentos e rendimentos preservados com suas datas ou meses originais. '
              'Os registros não são lançamentos de conta corrente. CSV e Excel disponíveis; OFX indisponível.')
    return {'schema_version': 'financial_events_v1', 'document_type': 'acquirer_sales_report' if origin == 'Stone'
            else 'investment_statement', 'origin': origin, 'evidence': [evidence], 'events': events or [],
            'observations': observations, 'validations': checks, 'metadata': {**metadata, 'summary_scope': 'per_block'},
            'notice': notice, 'export': {'available_formats': ['csv', 'xlsx'], 'ofx_profile': None,
            'requires_destination_account': False, 'supports_row_edits': False, 'reason': notice}}


def continuity(checks, prior, key, start, end, opening, closing, source_refs):
    if key in prior:
        previous_end, previous_close = prior[key]
        checks.append(_check('report_blocks_continuity', start == previous_end+1 and opening == previous_close,
                             True, refs=source_refs))
    prior[key] = (end, closing)


def parse_period_report_result(source, evidence, *, native=False):
    from app.application.financial_event_projection import project_financial_document
    from app.application.parsers.pdf.bb_monthly_funds import parse_bb_monthly_funds
    from app.application.parsers.pdf.sicredi_term_deposits import parse_sicredi_term_deposits
    from app.application.parsers.pdf.stone_payment_receipts import parse_stone_payment_receipts

    rows = visual_rows(source)
    reader = {'sicredi_term_deposit_monthly': parse_sicredi_term_deposits,
              'bb_monthly_fund_positions': parse_bb_monthly_funds, 'stone_payment_receipt_rows': parse_stone_payment_receipts}[evidence]
    report = reader(rows)
    pages = ['\n'.join(line.text for line in page.lines) for page in source.pages]
    result = project_financial_document(report, pages, native=native)
    return replace(result, source_extraction=source, source_layout_lines=tuple(tuple(p.lines) for p in source.pages))
