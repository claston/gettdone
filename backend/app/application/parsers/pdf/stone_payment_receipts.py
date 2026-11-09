"""Stone payment receipts preserve card modality and reported settlement status."""
import re
from datetime import date

from app.application.parsers.pdf.financial_reports import _check, _event
from app.application.parsers.pdf.period_report_source import (
    NUMBER,
    account,
    blocks,
    decimal,
    document,
    refs,
    safe_text,
)
from app.application.parsers.pdf.verified_statements import DATE, day, fail

HEADERS = ['DATA', 'VALOR', 'MODALIDADE', 'BANDEIRA', 'SITUACAO', 'IDENTIFICACAO', 'CODIGO']


def parse_stone_payment_receipts(rows):
    sections = blocks(rows, lambda t: t == 'COMPROVANTE DE PAGAMENTO',
                      lambda rs: any('ENTENDA A SITUACAO' in r.normalized for r in rs))
    events, checks, prior = [], [], {}
    for block, section in enumerate(sections, 1):
        periods = set(re.findall(rf'PERIODO:\s*DE\s+({DATE})\s+A\s+({DATE})', '\n'.join(r.normalized for r in section)))
        if len(periods) > 1:
            fail('Há períodos diferentes sem separação de bloco dos pagamentos Stone.')
        start, end = map(day, next(iter(periods))) if periods else (None, None)
        if start is None:
            checks.append(_check('stone_period_unconfirmed', False, True, refs=refs(section)))
        source_account = account(section)
        columns, pending, active, footer = None, [], False, False

        def emit():
            if not pending:
                return
            cells = {label: [] for label in HEADERS}
            for row in pending:
                for word in row.words:
                    candidates = [label for label in HEADERS if word.bbox['left'] >= columns[label]-.006]
                    if not candidates:
                        fail('Uma célula do comprovante Stone não pertence a uma coluna reconhecida.')
                    cells[candidates[-1]].append(word.text)
            values = {k: ' '.join(v) for k, v in cells.items()}
            if not all(values[k] for k in HEADERS[:-1]):
                fail('Um pagamento Stone está sem data, valor, modalidade, bandeira, situação ou identificação.')
            payment = day(values['DATA'])
            if start is not None and not start <= payment <= end:
                fail('Um pagamento Stone está fora do período informado.')
            amount = str(decimal(values['VALOR']))
            identifier = safe_text(''.join(cells['IDENTIFICACAO']))
            code = safe_text(''.join(cells['CODIGO'])) or None
            source_refs = refs(pending)
            checks.append(_check('stone_identifier_readable', '\\u' not in identifier and (not code or '\\u' not in code),
                                 True, refs=source_refs))
            events.append(_event('acquirer_payment', dates={'payment': payment}, amounts={'reported_payment': amount},
                primary='reported_payment', cash_effect=None, reference=identifier,
                description=f"Repasse Stone — {values['MODALIDADE']} {values['BANDEIRA']}", refs=source_refs,
                status='paid' if values['SITUACAO'].upper() == 'PAGO' else 'reported',
                attributes={'card_type': values['MODALIDADE'], 'card_brand': values['BANDEIRA'],
                            'printed_status': values['SITUACAO'], 'payment_code': code, 'block': block,
                            'source_account_number': source_account,
                            'source_fields': {k: safe_text(v) for k, v in values.items()}}))
            pending.clear()

        for row in section:
            t = row.normalized
            if all(label in t for label in HEADERS):
                from app.application.normalization.text import normalize_upper_text

                header = {normalize_upper_text(w.text): w.bbox['left'] for w in row.words}
                if not all(label in header for label in HEADERS):
                    fail('O cabeçalho das colunas Stone está incompleto.')
                columns = {label: header[label] for label in HEADERS}
                active = True
                continue
            if t.startswith('ENTENDA A SITUACAO DOS PAGAMENTOS'):
                emit()
                active, footer = False, True
                continue
            if re.match(r'^\d{2}/', t):
                if not columns or footer:
                    fail('Um pagamento está fora da tabela Stone reconhecida.')
                emit()
                pending.append(row)
            elif active:
                if not pending or any(w.bbox['left'] < columns['IDENTIFICACAO']-.006 for w in row.words):
                    fail('Uma continuação de pagamento Stone não pode ser associada ao identificador.')
                pending.append(row)
            elif re.search(NUMBER, row.text):
                fail('Há valores fora da tabela Stone reconhecida.')
        emit()
        if not any(e['attributes']['block'] == block for e in events):
            fail('O comprovante Stone não contém pagamentos legíveis.')
        checks.append(_check('stone_document_coverage', footer, True, refs=refs(section)))
        if start is not None and source_account in prior:
            checks.append(_check('stone_period_continuity', date.fromisoformat(start).toordinal() == prior[source_account]+1,
                                 True, refs=refs(section)))
        if source_account is not None and end is not None:
            prior[source_account] = date.fromisoformat(end).toordinal()
        for page in {r.page.page_number for r in section}:
            if not any(ref['page'] == page for e in events if e['attributes']['block'] == block for ref in e['source_refs']):
                fail('Uma página posterior está sem pagamentos Stone reconhecidos.')
    return document('Stone', 'stone_payment_receipt_rows', [], checks, {'blocks': len(sections)}, events=events)
