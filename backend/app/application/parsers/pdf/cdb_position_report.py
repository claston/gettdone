"""Column evidence for CDB snapshots: positions, purchase details and printed controls."""
import re
from collections import defaultdict
from dataclasses import replace
from decimal import Decimal
from statistics import median

from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.verified_statements import day, fail

EVIDENCE = "btg_cdb_positions_and_details"
ASSET = re.compile(r"CDB[A-Z0-9]{6,}")
DATE = r"\d{2}/\d{2}/\d{2}"
NUMBER = r"(?:\d{1,3}(?:[. ]\d{3})+|\d+),\d{1,12}"
MESSAGE = "Não foi possível associar todos os campos da posição de CDB às colunas impressas. Confira o documento original."


def parse_cdb_position_result(source, *, native=False):
    from app.application.financial_event_projection import project_financial_document

    pages = ['\n'.join(line.text for line in page.lines) for page in source.pages]
    result = project_financial_document(parse_cdb_position_report(source), pages, native=native)
    return replace(result, source_extraction=source, source_layout_lines=tuple(tuple(page.lines) for page in source.pages),
                   parse_metrics={**result.parse_metrics, 'document_coverage_validated': 1})


def _y(item):
    return item.bbox['top'] + item.bbox['height'] / 2


def _decimal(text, *, money=False):
    if text == "-":
        return Decimal(0)
    pattern = r"(?:\d{1,3}(?:[. ]\d{3})+|\d+),\d{2}" if money else NUMBER
    if not re.fullmatch(rf'[+-]?{pattern}', text):
        fail(MESSAGE)
    return Decimal(text.replace(' ', '').replace('.', '').replace(',', '.'))


def _refs(page, words):
    identifiers = {word.id for word in words}
    return [{'page': page.page_number, 'line': line.line_index} for line in page.lines if identifiers.intersection(line.word_ids)]


def _distinct_words(words):
    """Fold duplicate OCR evidence only when the same token occupies the same ink region."""
    result = []
    for word in words:
        def overlap(other):
            if word.text != other.text:
                return False
            a, b = word.bbox, other.bbox
            area = min(a['width']*a['height'], b['width']*b['height'])
            width = max(0, min(a['left']+a['width'], b['left']+b['width'])-max(a['left'], b['left']))
            height = max(0, min(a['top']+a['height'], b['top']+b['height'])-max(a['top'], b['top']))
            return area > 0 and width*height >= area*.8
        if not any(overlap(previous) for previous in result):
            result.append(word)
    return result


def _cell_text(words):
    if not words:
        return ''
    rows = []
    tolerance = median(word.bbox['height'] for word in words)*.5
    for word in sorted(_distinct_words(words), key=_y):
        if not rows or abs(_y(rows[-1][0])-_y(word)) > tolerance:
            rows.append([word])
        else:
            rows[-1].append(word)
    return ' '.join(word.text for row in rows for word in sorted(row, key=lambda item: item.bbox['left']))


def _columns(words, detail):
    def choices(label):
        return sorted([word for word in words if upper(word.text).lstrip('1234') == label], key=lambda w: w.bbox['left'])

    def one(label):
        found = choices(label)
        if len(found) != 1:
            fail(MESSAGE)
        return found[0].bbox['left']

    prices, balances = choices('PRECO'), choices('SALDO')
    if len(prices) != (2 if detail else 1) or len(balances) != 2:
        fail(MESSAGE)
    columns = {'asset': one('ATIVO'), 'issue': one('EMISSAO'), 'maturity': one('VENCIMENTO'),
               'liquidity': min(w.bbox['left'] for w in choices('LIQUIDEZ')) if choices('LIQUIDEZ') else one('LIQUIDEZ'),
               'grace_days': one('DIAS'), 'liquidity_date': one('DATA'), 'rate': one('TAXA'),
               'quantity': one('QUANTIDADE'), 'unit_price': prices[-1].bbox['left'],
               'gross': balances[0].bbox['left'], 'irrf': one('IR'), 'iof': one('IOF'), 'net': balances[1].bbox['left']}
    if detail:
        columns.update(application=one('AQUISICAO'), purchase_price=prices[0].bbox['left'], principal=one('VALOR'))
    else:
        columns['issuer'] = one('EMISSOR')
    return dict(sorted(columns.items(), key=lambda item: item[1]))


def _read_section(page, top, bottom, detail, previous_columns=None):
    words = [word for word in page.words if word.bbox and top < _y(word) < bottom]
    codes = [word for word in words if ASSET.fullmatch(word.text)]
    if not codes:
        fail(MESSAGE)
    first = min(_y(word) for word in codes)
    headers = [word for word in words if _y(word) < first]
    has_headers = any(upper(word.text) == 'EMISSAO' for word in headers)
    columns = _columns(headers, detail) if has_headers else previous_columns
    if not columns:
        fail(MESSAGE)
    names, starts = list(columns), list(columns.values())

    def role(word):
        return names[max(0, sum(word.bbox['left'] >= x-.004 for x in starts)-1)]

    header_words = [word for word in headers if upper(word.text).lstrip('1234') in {
        'EMISSAO', 'VENCIMENTO', 'AQUISICAO', 'LIQUIDEZ', 'DIAS', 'DATA', 'TAXA', 'QUANTIDADE', 'PRECO', 'SALDO', 'IR', 'IOF'}]
    low = max(_y(word) for word in header_words) if has_headers else first-max(word.bbox['height'] for word in codes)*3
    words = [word for word in words if _y(word) > low + .003]
    dates = sorted([word for word in words if role(word) == 'issue' and re.fullmatch(DATE, word.text)], key=_y)
    if not dates:
        fail(MESSAGE)
    tolerance = max(.002, median(word.bbox['height'] for word in dates)*.8)
    used, used_codes, rows = set(), set(), []
    for index, anchor in enumerate(dates):
        lower = (_y(dates[index-1])+_y(anchor))/2 if index else low+.003
        high = (_y(anchor)+_y(dates[index+1]))/2 if index+1 < len(dates) else _y(anchor)+tolerance*3
        cells = [word for word in words if lower < _y(word) < high and
                 (role(word) in {'asset', 'issuer'} or abs(_y(word)-_y(anchor)) <= tolerance)]
        grouped = {name: sorted([word for word in cells if role(word) == name], key=lambda w: (_y(w), w.bbox['left']))
                   for name in names}
        asset = [word for word in grouped['asset'] if ASSET.fullmatch(word.text)]
        if len(asset) != 1:
            fail(MESSAGE)
        values = {name: _cell_text(group) for name, group in grouped.items()}
        amounts = {name: _decimal(values[name], money=True) for name in ('gross', 'net')}
        for name in ('irrf', 'iof', *(['principal'] if detail else [])):
            amounts[name] = _decimal(values[name], money=True) if values[name] else None
        dates_value = {name: day(values[name], '%d/%m/%y') for name in ('issue', 'maturity', 'liquidity_date',
                                                                                   *(['application'] if detail else []))}
        if upper(values['liquidity']) not in {'SIM', 'NAO'} or not re.fullmatch(r'\d+', values['grace_days']):
            fail(MESSAGE)
        attributes = {name: str(_decimal(values[name])) for name in ('quantity', 'unit_price', *(['purchase_price'] if detail else []))}
        attributes.update(asset_code=asset[0].text, rate=values['rate'], liquidity=values['liquidity'],
                          grace_days=values['grace_days'], source_values=values)
        if not values['rate']:
            fail(MESSAGE)
        if not detail:
            attributes['issuer'] = values['issuer']
        used.update(w.id for w in cells)
        used_codes.add(asset[0].id)
        rows.append({'amounts': amounts, 'dates': dates_value, 'attributes': attributes, 'source_refs': _refs(page, cells)})
    if used_codes != {word.id for word in codes}:
        fail("Há um ativo de CDB sem seus campos legíveis; a extração não pode ser apresentada como completa.")
    totals_words = [word for word in words if word.id not in used and role(word) in {'gross', 'irrf', 'iof', 'net', 'principal'}
                    and re.fullmatch(rf'(?:{NUMBER}|-)', word.text)]
    totals = {}
    for name in ('gross', 'irrf', 'iof', 'net', *(['principal'] if detail else [])):
        found = [word for word in totals_words if role(word) == name]
        if len(found) > 1:
            fail(MESSAGE)
        totals[name] = _decimal(found[0].text, money=True) if found else None
    return rows, totals, _refs(page, totals_words), columns


def parse_cdb_position_report(source):
    observations, validations = [], []
    positions, details = {}, defaultdict(list)
    context, previous_columns, previous_detail = None, None, False
    covered = set()
    aggregates, controls = defaultdict(list), defaultdict(list)
    all_text = ' '.join(line.text for page in source.pages for line in page.lines)
    accounts = set(re.findall(r'CONTA INVESTIMENTO\s*:?\s*([\d-]+)', upper(all_text)))
    if len(accounts) > 1:
        fail("A associação das posições às contas de investimento não está inequívoca neste documento.")
    def check(code, actual, expected, refs):
        passed = actual is not None and expected is not None and actual == expected
        validations.append({'code': code, 'status': 'passed' if passed else 'failed',
                            'actual': str(actual) if actual is not None else 'Não legível',
                            'expected': str(expected) if expected is not None else 'Campo impresso não legível', 'source_refs': refs})

    for page in source.pages:
        if not page.lines and not source.metrics.get('empty_native_pages_verified'):
            fail("Uma página do relatório de posições não pôde ser lida. A extração não pode ser apresentada como completa.")
        text = ' '.join(line.text for line in page.lines)
        periods = set(re.findall(rf'PERIODO DE\s+({DATE})\s+(?:A|ATE)\s+({DATE})', upper(text)))
        if len(periods) > 1:
            fail("Há períodos de posição sem associação inequívoca às tabelas de CDB.")
        if periods:
            context = tuple(day(value, '%d/%m/%y') for value in periods.pop())
        disclaimer = (bool(page.lines) and upper(page.lines[0].text) == 'DISCLAIMERS' and
                      not any(re.fullmatch(rf'[+-]?{NUMBER}', word.text) for word in page.words))
        unsupported = [upper(line.text) for line in page.lines if not disclaimer and re.match(
            r'^(?:CONTA CORRENTE\s*-\s*MOVIMENTACAO|(?:RENDA FIXA|RENDA VARIAVEL|FUNDOS? DE INVESTIMENTO)\s*-\s*(?:POSICAO|DETALHAMENTO))',
            upper(line.text)) and not ('RENDA FIXA' in upper(line.text) and
                                      ('CDB' in upper(line.text) or 'CONSOLIDADA POR EMISSOR' in upper(line.text)))]
        if unsupported:
            fail("Este relatório inclui outra seção financeira que precisa de leitura própria para evitar uma extração parcial.")
        headings = sorted([line for line in page.lines if line.bbox and 'CDB' in upper(line.text)
                           and any(label in upper(line.text) for label in ('POSICAO', 'DETALHAMENTO'))], key=_y)
        if not headings and any(ASSET.search(line.text) for line in page.lines):
            if previous_columns is None or context is None:
                fail(MESSAGE)
            headings = [None]
        elif headings and previous_columns and any(ASSET.search(line.text) and _y(line) < _y(headings[0])
                                                   for line in page.lines if line.bbox):
            headings.insert(0, None)
        for index, heading in enumerate(headings):
            if heading is not None and 'CONSOLIDADA' in upper(heading.text):
                continue
            if context is None or context[0] > context[1]:
                fail("A posição de CDB está sem período legível; datas de aplicação ou liquidez não substituem sua data de posição.")
            detail = 'DETALHAMENTO' in upper(heading.text) if heading is not None else previous_detail
            top = _y(heading) if heading is not None else 0
            bottom = _y(headings[index+1]) if index+1 < len(headings) else 1
            # A consolidated issuer section can omit CDB in its title.
            stops = [_y(line) for line in page.lines if line.bbox and _y(line) > top
                     and 'POSICAO CONSOLIDADA' in upper(line.text)]
            bottom = min([bottom, *stops])
            rows, totals, total_refs, previous_columns = _read_section(page, top, bottom, detail, previous_columns)
            previous_detail = detail
            aggregates[(detail, context[1])].extend(rows)
            if any(value is not None for value in totals.values()):
                controls[(detail, context[1])].append((totals, total_refs))
            for row in rows:
                row['dates'].update(as_of=context[1], period_start=context[0], period_end=context[1])
                check('cdb_printed_date_order', row['dates']['issue'] <= row['dates']['maturity'], True, row['source_refs'])
                key = (context[1], row['attributes']['asset_code'])
                covered.update((ref['page'], ref['line']) for ref in row['source_refs'])
                if detail:
                    details[key].append(row)
                elif key in positions:
                    fail("Há posições repetidas para o mesmo ativo e data; a origem precisa ser conferida.")
                else:
                    positions[key] = row
                tax = row['amounts']
                check('cdb_net_position', tax['gross']-tax['irrf']-tax['iof']
                      if tax['irrf'] is not None and tax['iof'] is not None else None, tax['net'], row['source_refs'])
            if total_refs:
                observations.append({'observation_type': 'investment_section_total', 'dates': {'as_of': context[1]},
                    'amounts': {('closing_'+k if not detail and k in {'gross', 'net'} else k): format(v, '.2f')
                                for k, v in totals.items() if v is not None},
                    'attributes': {'section': 'purchase_details' if detail else 'positions',
                                   'label': 'Total do detalhamento de CDB' if detail else 'Total das posições de CDB'},
                    'source_refs': total_refs})
    if not positions or any(key not in positions for key in details):
        fail(MESSAGE)
    if any((page.page_number, line.line_index) not in covered for page in source.pages
           for line in page.lines if ASSET.search(line.text)):
        fail("Há ativos em uma seção que não pôde ser associada. A extração não pode ser apresentada como completa.")
    for key, rows in aggregates.items():
        for totals, total_refs in controls[key] or [({}, [])]:
            for field in ('gross', 'irrf', 'iof', 'net', *(['principal'] if key[0] else [])):
                values = [row['amounts'][field] for row in rows]
                actual = sum(values, Decimal(0)) if all(value is not None for value in values) else None
                check('cdb_printed_total_'+field, actual, totals.get(field), total_refs)
    for key, position in positions.items():
        lots = details.get(key, [])
        if lots:
            for field in ('gross', 'irrf', 'iof', 'net'):
                values = [lot['amounts'][field] for lot in lots]
                check('cdb_position_detail_'+field, sum(values, Decimal(0)) if all(v is not None for v in values) else None,
                      position['amounts'][field], [ref for lot in lots for ref in lot['source_refs']])
            check('cdb_position_detail_quantity', sum(Decimal(lot['attributes']['quantity']) for lot in lots),
                  Decimal(position['attributes']['quantity']), [ref for lot in lots for ref in lot['source_refs']])
            for lot in lots:
                check('cdb_position_detail_unit_price', Decimal(lot['attributes']['unit_price']),
                      Decimal(position['attributes']['unit_price']), lot['source_refs'])
                position['source_refs'] += lot['source_refs']
                observations.append({'observation_type': 'investment_lot', 'dates': lot['dates'],
                    'amounts': {k: format(v, '.2f') for k, v in lot['amounts'].items() if v is not None},
                    'attributes': {**lot['attributes'], 'label': 'Aquisição de '+key[1]}, 'source_refs': lot['source_refs']})
            if len(lots) == 1:
                position['dates']['application'] = lots[0]['dates']['application']
                position['amounts']['principal'] = lots[0]['amounts']['principal']
                position['attributes']['purchase_price'] = lots[0]['attributes']['purchase_price']
                position['attributes']['purchase_source_values'] = lots[0]['attributes']['source_values']
        position['attributes']['label'] = 'Posição de '+key[1]
        position['amounts'] = {k: format(v, '.2f') for k, v in position['amounts'].items() if v is not None}
        observations.append({'observation_type': 'fixed_income_position', **position})
    observations.extend(_supplementary(source, positions, check))
    notice = ('Posições de CDB: ativos, quantidades, preços, datas, impostos e saldos estão preservados no CSV e Excel. '
              'Posições, valores de compra históricos e totais não são movimentos bancários. OFX indisponível para este relatório.')
    if any(check['status'] == 'failed' for check in validations):
        notice += ' Há campos ou totais que exigem revisão; consulte as validações.'
    return {'schema_version': 'financial_events_v1', 'document_type': 'investment_statement', 'origin': 'BTG Pactual',
            'evidence': [EVIDENCE], 'events': [], 'observations': observations, 'validations': validations,
            'metadata': {'product': 'CDB', 'position_count': len(positions),
                         'period_start': min(row['dates']['period_start'] for row in positions.values()),
                         'period_end': max(row['dates']['period_end'] for row in positions.values()),
                         **({'product_account': next(iter(accounts))} if accounts else {})}, 'notice': notice,
            'export': {'available_formats': ['csv', 'xlsx'], 'ofx_profile': None, 'requires_destination_account': False,
                       'supports_row_edits': False, 'reason': notice}}


def _supplementary(source, positions, check):
    """Keep printed aggregates and associated account/bucket positions out of cash events."""
    result = []
    dates = {key[0] for key in positions}
    for page in source.pages:
        lines = sorted([line for line in page.lines if line.bbox], key=_y)
        text = upper(' '.join(line.text for line in lines))
        printed = re.findall(rf'PERIODO DE\s+({DATE})\s+(?:A|ATE)\s+({DATE})', text)
        as_of = day(printed[0][1], '%d/%m/%y') if len(printed) == 1 else next(iter(dates)) if len(dates) == 1 else None

        def observation(kind, amounts, label, cells, **attributes):
            if as_of is None:
                fail(MESSAGE)
            return {'observation_type': kind, 'dates': {'as_of': as_of},
                'amounts': {key: format(value, '.2f') for key, value in amounts.items()},
                'attributes': {'label': label, **attributes},
                'source_refs': [{'page': page.page_number, 'line': line.line_index} for line in cells]}

        for heading in lines:
            label = upper(heading.text)
            if 'POSICAO CONSOLIDADA POR EMISSOR' in label:
                numeric = [line for line in lines if _y(line) > _y(heading)
                           and re.fullmatch(r'\d[\d.]*,\d{2}', line.text)]
                for value in numeric:
                    names = [line for line in lines if line.bbox['left'] < value.bbox['left']-.1
                             and abs(_y(line)-_y(value)) < .008]
                    if not names:
                        fail(MESSAGE)
                    name = ' '.join(line.text for line in names)
                    total = _decimal(value.text, money=True)
                    matching = [row for (date, _), row in positions.items() if date == as_of and
                                re.sub(r'\W', '', upper(row['attributes']['issuer'])) == re.sub(r'\W', '', upper(name))]
                    check('cdb_issuer_total', sum(Decimal(row['amounts']['gross']) for row in matching) if matching else None,
                          total, [{'page': page.page_number, 'line': value.line_index}])
                    result.append(observation('issuer_position', {'gross': total}, name, [*names, value]))
            elif label.startswith('SUMARIO') and 'DISTRIBUICAO' in label:
                labels = [line for line in lines if _y(line) > _y(heading) and upper(line.text) == 'RENDA FIXA']
                totals = [line for line in lines if _y(line) > _y(heading) and upper(line.text) == 'TOTAL']
                if len(labels) != 1 or len(totals) != 1:
                    fail("O sumário da carteira contém categorias não cobertas por esta leitura de CDB.")
                fields = ('opening_gross', 'opening_net', 'closing_gross', 'closing_net')
                parsed, source_refs = [], []
                for anchor in [labels[0], totals[0]]:
                    cells = sorted([word for word in page.words if word.bbox and abs(_y(word)-_y(anchor)) < .008
                                    and re.fullmatch(r'\d[\d.]*,\d{2}', word.text)], key=lambda word: word.bbox['left'])
                    if len(cells) != 4:
                        fail(MESSAGE)
                    parsed.append([_decimal(word.text, money=True) for word in cells])
                    source_refs.extend(_refs(page, cells))
                for field, actual, expected in zip(fields, parsed[0], parsed[1], strict=True):
                    check('cdb_portfolio_'+field, actual, expected, [{'page': page.page_number, 'line': labels[0].line_index}])
                ending = [row for (date, _), row in positions.items() if date == as_of]
                for field, total in [('gross', parsed[0][2]), ('net', parsed[0][3])]:
                    check('cdb_portfolio_position_'+field, sum(Decimal(row['amounts'][field]) for row in ending), total,
                          [{'page': page.page_number, 'line': labels[0].line_index}])
                header_dates = [day(word.text, '%d/%m/%y') for word in page.words if word.bbox
                                and _y(heading) < _y(word) < _y(labels[0]) and re.fullmatch(DATE, word.text)]
                if len(header_dates) != 4:
                    fail(MESSAGE)
                item = observation('portfolio_position_summary', dict(zip(fields, parsed[0], strict=True)),
                                   'Sumário da carteira de renda fixa', [labels[0], totals[0]])
                item['dates']['opening_as_of'] = header_dates[0]
                item['source_refs'] = source_refs
                check('cdb_portfolio_date_columns', header_dates[0] == header_dates[1] and
                      header_dates[2] == header_dates[3] == as_of and header_dates[0] < as_of, True, source_refs)
                result.append(item)
            elif label.startswith('COFRINHO - POSICOES'):
                stop = min([_y(line) for line in lines if _y(line) > _y(heading) and 'CONTA CORRENTE' in upper(line.text)], default=1)
                body = [word for word in page.words if word.bbox and _y(heading) < _y(word) < stop]
                anchors = [word for word in body if re.fullmatch(DATE, word.text)]
                for anchor in anchors:
                    cells = sorted([word for word in body if abs(_y(word)-_y(anchor)) < .008], key=lambda word: word.bbox['left'])
                    cash = [word for word in cells if re.fullmatch(r'\d[\d.]*,\d{2}', word.text)]
                    if len(cash) != 2:
                        fail(MESSAGE)
                    name = ' '.join(word.text for word in cells if word.bbox['left'] < cash[0].bbox['left'])
                    item = observation('investment_bucket_position', {'net': _decimal(cash[0].text, money=True),
                        'gross': _decimal(cash[1].text, money=True)}, name,
                        [line for line in lines if set(line.word_ids).intersection(word.id for word in cells)])
                    item['dates']['maturity'] = day(anchor.text, '%d/%m/%y')
                    result.append(item)
            elif label.startswith('CONTA CORRENTE - POSICAO'):
                anchors = [line for line in lines if _y(line) > _y(heading) and re.fullmatch(DATE, line.text)]
                for anchor in anchors:
                    cells = [word for word in page.words if word.bbox and word.bbox['left'] > anchor.bbox['left']+.1
                             and abs(_y(word)-_y(anchor)) < .008]
                    value = ' '.join(word.text for word in cells)
                    if not value:
                        fail(MESSAGE)
                    item = observation('cash_position', {} if value == '-' else {'net': _decimal(value, money=True)},
                                       'Posição de conta corrente', [anchor], printed_value=value)
                    item['source_refs'] += _refs(page, cells)
                    item['dates']['as_of'] = day(anchor.text, '%d/%m/%y')
                    result.append(item)
    return result
