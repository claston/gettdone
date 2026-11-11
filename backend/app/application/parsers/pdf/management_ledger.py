"""A shared, geometry-based reader for management account ledgers, not bank brands."""
import json
import re
from bisect import bisect_right
from dataclasses import replace
from datetime import date, timedelta
from statistics import median

from app.application.bank_catalog import load_bank_catalog, resolve_bank_code_from_name
from app.application.financial_event_projection import project_financial_document
from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.financial_reports import _check, _event
from app.application.parsers.pdf.period_report_source import decimal, refs, visual_rows
from app.application.parsers.pdf.verified_statements import fail
from app.application.source_regions import evidence_regions

EVIDENCE = 'management_ledger_status_category_columns'
LABELS = ('Situação', 'Data', 'Cliente ou Fornecedor', 'Documento', 'Categoria', 'Valor', 'Saldo')
HEADER = ('SITUACAO', 'DATA', 'CLIENTE', 'DOCUMENTO', 'CATEGORIA', 'VALOR', 'SALDO')
MESSAGE = 'Há um campo sem associação inequívoca no relatório gerencial. Confira o documento original completo.'


def _full_date(text):
    try:
        day, month, year = map(int, text.split('/'))
        return date(year, month, day)
    except (ValueError, TypeError):
        fail(MESSAGE)


def _row_date(text, start, end, *, opening=False):
    if re.fullmatch(r'\d{2}/\d{2}/\d{4}', text):
        return _full_date(text)
    if not re.fullmatch(r'\d{2}/\d{2}', text) or start is None or end is None:
        fail(MESSAGE)
    candidates = []
    day, month = map(int, text.split('/'))
    for year in range(start.year - int(opening), end.year + 1):
        try:
            value = date(year, month, day)
        except ValueError:
            continue
        if (start-timedelta(days=31) <= value < start) if opening else (start <= value <= end):
            candidates.append(value)
    if len(candidates) != 1:
        fail('O mês ou ano de um lançamento não pode ser determinado pelo período impresso.')
    return candidates[0]


def _columns(row):
    if not set(HEADER).issubset({upper(word.text) for word in row.words}):
        return None
    positions = {}
    for word in row.words:
        if upper(word.text) in HEADER:
            if upper(word.text) in positions:
                fail(MESSAGE)
            positions[upper(word.text)] = word.bbox['left']
    if set(positions) != set(HEADER):
        return None
    x = [positions[key] for key in HEADER]
    if x != sorted(x) or len(set(x)) != 7:
        fail(MESSAGE)
    # Text columns are left-aligned; values are right-aligned in wider cells.
    gap = x[6]-x[5]
    return [(x[0]+x[1])/2, (x[1]+x[2])/2, x[3]-.06*gap, x[4]-.06*gap,
            x[5]-.5*gap, x[6]-.5*gap]


def _fields(row, boundaries, *, text_objects=False):
    columns = [[] for _ in LABELS]
    if text_objects:
        # Original PDF objects keep overlapping party/document strings separate.
        # Sorting their glyphs together would scramble otherwise readable text.
        words = {word.id: word for word in row.words}
        for line in row.page.lines:
            group = [words[key] for key in line.word_ids if key in words]
            if not group:
                continue
            if len(group) != len(line.word_ids):
                fail(MESSAGE)
            columns[bisect_right(boundaries, group[0].bbox['left'])].extend(word.text for word in group)
        return [' '.join(words) for words in columns]
    groups = []
    tolerance = median(word.bbox['height'] for word in row.words)*.5
    for word in row.words:
        previous = groups[-1][-1] if groups else None
        gap = word.bbox['left']-(previous.bbox['left']+previous.bbox['width']) if previous else None
        if gap is not None and gap < -tolerance:
            fail(MESSAGE)
        if gap is not None and gap <= tolerance:
            groups[-1].append(word)
        else:
            groups.append([word])
    # Long names can occupy an empty reference cell. Assign coherent text runs,
    # not individual words; otherwise the end of a name becomes a fake document.
    for group in groups:
        columns[bisect_right(boundaries, group[0].bbox['left'])].extend(word.text for word in group)
    return [' '.join(words) for words in columns]


def _context(rows, previous):
    text = '\n'.join(row.normalized for row in rows)
    titles = [row.text[len('Extrato de '):] for row in rows if row.normalized.startswith('EXTRATO DE ')]
    periods = re.findall(r'PERIODO DE (\d{2}/\d{2}/\d{4}) ATE (\d{2}/\d{2}/\d{4})', text)
    if len(titles) > 1 or len(periods) > 1:
        fail(MESSAGE)
    context = dict(previous or {})
    if titles:
        context = {'title': titles[0], 'start': None, 'end': None}
    if periods:
        context.update(start=_full_date(periods[0][0]), end=_full_date(periods[0][1]))
    elif 'PERIODO' in text:
        context.update(start=None, end=None)
    kinds = [row.text for row in rows if row.normalized in {'CONTA CORRENTE', 'ADMINISTRADORA DE CARTOES'}]
    if kinds:
        context['kind'] = kinds[0]
    accounts = re.findall(r'AGENCIA:\s*([\d.-]+)\s*/\s*CONTA:\s*([\d.-]+)', text)
    if accounts:
        if len(set(accounts)) != 1:
            fail(MESSAGE)
        context['branch'], context['account'] = accounts[0]
    elif 'AGENCIA' in text or 'CONTA:' in text:
        fail(MESSAGE)
    banks = re.findall(r'^BANCO:\s*(.+)$', text, re.M)
    if banks:
        if len(set(banks)) != 1:
            fail(MESSAGE)
        code = re.fullmatch(r'(\d{3})(?:\s+.*)?', banks[0])
        context['bank_code'] = (code[1] if code and code[1] in {b.code for b in load_bank_catalog()}
                                else resolve_bank_code_from_name(banks[0]) if not code else None)
    elif titles:
        context['bank_code'] = resolve_bank_code_from_name(titles[0])
    title_bank = resolve_bank_code_from_name(context.get('title'))
    if title_bank and context.get('bank_code') and title_bank != context['bank_code']:
        context['bank_code'] = None
    if (not all(key in context for key in ('title', 'kind'))
            or context.get('start') is not None and context['start'] > context['end']):
        fail(MESSAGE)
    counters = re.findall(r'\(PAGINA\s+(\d+)/(\d+)\)', text)
    context['counter'] = tuple(map(int, counters[0])) if len(counters) == 1 else None
    return context


def _identity(context):
    if (upper(context.get('kind', '')) == 'CONTA CORRENTE' and context.get('branch') and context.get('account')
            and not re.search(r'\bCART(?:AO|OES)\b', upper(context.get('title', '')))):
        return ('current_account', context.get('bank_code') or ('unconfirmed:' + upper(context['title'])),
                context['branch'], context['account'])
    return tuple(upper(context.get(key, '')) for key in ('title', 'kind', 'branch', 'account'))


def parse_management_ledger_result(source, *, native=False):
    rows = visual_rows(source)
    blocks, block, context, boundaries, pending = [], None, None, None, []
    events, observations, checks, event_rows = [], [], [], []
    for row in rows:
        normalized = row.normalized
        columns = _columns(row)
        if columns is not None:
            new = _context(pending, context)
            independent = (block is None or _identity(new) != _identity(context)
                           or (new['start'], new['end']) != (context['start'], context['end'])
                           or new['counter'] is not None and new['counter'][0] == 1)
            if independent:
                block = {'context': new, 'events': [], 'opening': None, 'closing': None, 'rows': [], 'counters': [], 'pages': set()}
                blocks.append(block)
            block['counters'].append(new['counter'])
            block['pages'].add(row.page.page_number)
            context, boundaries, pending = new, columns, []
            continue
        if normalized.startswith(('EXTRATO DE ', 'BANCO:')) or pending or boundaries is None:
            pending.append(row)
            continue
        footer = re.fullmatch(r'PAGINA (\d+) DE (\d+)', normalized)
        if footer:
            checks.append(_check('management_footer_counter', tuple(map(int, footer.groups())), context['counter'], refs=row.refs))
            continue
        if normalized.startswith('GERADO EM '):
            continue
        fields = _fields(row, boundaries, text_objects=bool(source.metrics.get('native_text_objects_preserved')))
        status, day, party, reference, category, value, balance = fields
        block['pages'].add(row.page.page_number)
        block['rows'].append(row)
        if upper(party) == 'SALDO ANTERIOR':
            if status or reference or category or value:
                fail(MESSAGE)
            if block['opening'] is not None or block['events']:
                block = {'context': context, 'events': [], 'opening': None, 'closing': None, 'rows': [row],
                         'counters': [], 'pages': {row.page.page_number}}
                blocks.append(block)
            amount = decimal(balance)
            when = _row_date(day, context['start'], context['end'], opening=True)
            checks.append(_check('management_opening_date', when,
                context['start']-timedelta(days=1) if context['start'] is not None else None, refs=row.refs))
            block['opening'] = block['closing'] = amount
            observations.append({'observation_type': 'opening_balance', 'dates': {'as_of': when.isoformat()},
                'amounts': {'opening': format(amount, '.2f')}, 'attributes': {'block': len(blocks),
                    'subaccount': context['title'], 'source_fields': dict(zip(LABELS, fields, strict=True))}, 'source_refs': row.refs})
            continue
        if not status and not day and not value and not balance:
            # Wrapped text belongs only to the preceding row of this same block.
            if not block['events'] or not any((party, reference, category)):
                fail(MESSAGE)
            event = block['events'][-1]
            for key, label, addition in (('counterparty', LABELS[2], party), ('reference', LABELS[3], reference)):
                if addition:
                    event[key] = ' '.join(filter(None, (event[key], addition)))
                    event['attributes']['source_fields'][label] = event[key]
            if category:
                event['attributes']['category'] = ' '.join(filter(None, (event['attributes']['category'], category)))
                event['attributes']['source_fields'][LABELS[4]] = event['attributes']['category']
            event['description'] = ' | '.join(filter(None, (event['counterparty'], event['attributes']['category'], event['reference'])))
            event_rows[-1].append(row)
            event['source_refs'] = refs(event_rows[-1])
            continue
        if not status or not day or not party or not value or not balance:
            fail(MESSAGE)
        amount, running = decimal(value), decimal(balance)
        when = _row_date(day, context['start'], context['end'])
        checks.append(_check('management_row_balance', running,
                             block['closing']+amount if block['closing'] is not None else None, refs=row.refs))
        checks.append(_check('management_row_within_period',
            context['start'] is not None and context['start'] <= when <= context['end'], True, refs=row.refs))
        settled = upper(status) == 'CONCILIADO'
        checks.append(_check('management_row_reconciled', settled, True, refs=row.refs))
        event = _event('cash_movement', dates={'transaction': when.isoformat()},
            amounts={'movement': format(amount, '.2f'), 'balance': format(running, '.2f')}, primary='movement',
            cash_effect=format(amount, '.2f') if settled else None, reference=reference or None, counterparty=party,
            description=' | '.join(filter(None, (party, category, reference))), refs=row.refs,
            status='reconciled' if settled else 'reported', attributes={'category': category, 'printed_status': status,
                'subaccount': context['title'], 'account_kind': context['kind'], 'source_account_number': context.get('account'),
                'source_bank_branch': context.get('branch'), 'source_bank_code': context.get('bank_code'), 'block': len(blocks),
                'source_fields': dict(zip(LABELS, fields, strict=True))})
        block['closing'] = running
        block['events'].append(event)
        events.append(event)
        event_rows.append([row])
    if pending or not events or any(not item['events'] for item in blocks):
        fail('Uma parte do relatório está sem lançamentos legíveis; a extração não pode ser apresentada como completa.')
    prior, metadata = {}, []
    coverage = True
    for index, block in enumerate(blocks, 1):
        context = block['context']
        counters = block['counters']
        complete = bool(counters) and all(c is not None for c in counters)
        if complete:
            total = counters[0][1]
            complete = counters == [(i, total) for i in range(1, total+1)] and len(block['pages']) == total
        period_known = context['start'] is not None and context['end'] is not None
        coverage &= complete and period_known
        source_refs = refs(block['rows'])
        checks.append(_check('management_block_coverage', complete, True, refs=source_refs))
        checks.append(_check('management_opening_available', block['opening'] is not None, True, refs=source_refs))
        key = _identity(context)
        checks.append(_check('management_period_coverage', period_known, True, refs=source_refs))
        if key[0] == 'current_account':
            checks.append(_check('management_source_institution', bool(context.get('bank_code')), True, refs=source_refs))
        if key in prior:
            end, closing = prior[key]
            checks.append(_check('management_blocks_continuity',
                period_known and end is not None and context['start'] == end+timedelta(days=1)
                and block['opening'] == closing, True, refs=source_refs))
        prior[key] = context['end'], block['closing']
        metadata.append({'block': index, 'account': context['title'], 'kind': context['kind'],
            'source_bank_code': context.get('bank_code'),
            'period_start': context['start'].isoformat() if period_known else None,
            'period_end': context['end'].isoformat() if period_known else None,
            'opening_balance': str(block['opening']), 'closing_balance': str(block['closing']), 'pages': sorted(block['pages'])})
    failed = any(c['status'] == 'failed' for c in checks)
    ofx = (not failed and len(prior) == 1 and upper(context['kind']) == 'CONTA CORRENTE'
           and not re.search(r'\bCART(?:AO|OES)\b', upper(context['title']))
           and bool(context.get('account') and context.get('branch')))
    notice = ('Relatório gerencial: situação, categoria, documento e saldos impressos preservados. '
              'A conciliação informada pertence ao relatório e não comprova liquidação bancária. ')
    notice += ('OFX disponível para os movimentos conciliados da conta corrente; confirme a conta de destino.' if ofx else
               'CSV e Excel disponíveis. OFX indisponível para esta natureza de conta ou enquanto houver validações pendentes.')
    document = {'schema_version': 'financial_events_v1', 'document_type': 'cash_flow_report', 'origin': 'Relatório gerencial',
        'evidence': [EVIDENCE], 'events': events, 'observations': observations, 'validations': checks,
        'metadata': {'summary_scope': 'per_block', 'blocks': json.dumps(metadata, ensure_ascii=False)}, 'notice': notice,
        'export': {'available_formats': ['csv', 'xlsx', 'ofx'] if ofx else ['csv', 'xlsx'],
                   'ofx_profile': 'receipts' if ofx else None, 'requires_destination_account': ofx,
                   'supports_row_edits': False, 'reason': notice}}
    pages = ['\n'.join(line.text for line in page.lines) for page in source.pages]
    result = project_financial_document(document, pages, native=native)
    canonical = [replace(item, source_regions=list(dict.fromkeys(region for row in origin
        for region in evidence_regions(row.page.page_number, row.words))))
        for item, origin in zip(result.canonical_transactions, event_rows, strict=True)]
    return replace(result, canonical_transactions=canonical, source_extraction=source,
        source_layout_lines=tuple(tuple(page.lines) for page in source.pages), parse_metrics={**result.parse_metrics,
            'document_coverage_validated': int(coverage), 'partial_statement': int(not coverage),
            'financial_controls_validated': int(not failed), 'statement_validation_failed': int(failed)})
