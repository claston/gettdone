"""Documentary suggestions only: never allocate payments or alter movements."""
import re
from datetime import date
from decimal import Decimal, InvalidOperation


def _money(value):
    try:
        parsed = Decimal(str(value))
        return abs(parsed) if parsed.is_finite() else None
    except InvalidOperation:
        return None


def _date(value):
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def document_evidence(financial):
    amounts, dates, identifiers = set(), set(), set()
    currency = (financial.get('metadata') or {}).get('currency')
    for observation in financial.get('observations', []):
        amounts.update(_money(value) for key, value in observation.get('amounts', {}).items()
                       if key in {'value', 'paid_amount', 'net', 'gross'})
        dates.update(_date(value) for value in observation.get('dates', {}).values())
        attrs = observation.get('attributes') or {}
        for key in ('transaction_id', 'end_to_end_id'):
            identifier = str(attrs.get(key) or '').strip().upper()
            if re.fullmatch(r'[A-Z0-9]{16,64}', identifier):
                identifiers.add(identifier)
        text = str(attrs.get('value') or '')
        if 'R$' in text and currency is None:
            currency = 'BRL'
        if attrs.get('label') == 'Identificação da operação':
            identifiers.update(re.findall(r'\bE\d{8}[A-Z0-9]{23}\b', text.upper()))
    return amounts - {None}, dates - {None}, identifiers, currency


def document_matches(financial, movements, *, complete=True):
    amounts, dates, identifiers, currency = document_evidence(financial)
    matches = []
    for movement in movements:
        if currency and currency != movement['currency']:
            continue
        attributes = movement.get('attributes') or {}
        tokens = set(re.findall(r'[A-Z0-9]{16,64}', ' '.join(str(attributes.get(k) or '')
                     for k in ('document_number', 'end_to_end_id', 'transaction_id')).upper()))
        tokens.update(re.findall(r'[A-Z0-9]{16,64}', movement['description'].upper()))
        identifier_match = bool(identifiers & tokens)
        amount_match = _money(movement['amount']) in amounts
        day = _date(movement['date'])
        date_match = bool(day and dates and min(abs((day - value).days) for value in dates) <= 3)
        if not identifier_match and not (amount_match and date_match):
            continue
        evidence = []
        if identifier_match:
            evidence.append('Mesmo identificador da operação')
        if amount_match:
            evidence.append('Mesmo valor')
        if date_match:
            evidence.append('Data compatível')
        reversal = bool(re.search(r'estorno|revers[aã]o|devolu[cç][aã]o', movement['description'], re.I))
        matches.append({key: movement[key] for key in ('id', 'version', 'date', 'amount', 'currency', 'description')} |
                       {'evidence': evidence, 'automatic': bool(identifier_match and amount_match and date_match and
                        currency and len(amounts) == len(dates) == 1 and not reversal and complete)})
    if len(matches) != 1:
        for match in matches:
            match['automatic'] = False
    return matches
