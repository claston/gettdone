from app.application.document_matching import document_matches


def evidence(amount='100.00', identifier='E1234567820261127012345678901234', date='2026-11-27'):
    return {'metadata': {'currency': 'BRL'}, 'observations': [{'amounts': {'value': amount},
        'dates': {'payment_date': date}, 'attributes': {'transaction_id': identifier}}]}


def movement(**values):
    return {'id': 'one', 'version': 1, 'amount': '-100.00', 'currency': 'BRL', 'date': '2026-11-27',
            'description': 'Pagamento', 'attributes': {'document_number': 'E1234567820261127012345678901234'}, **values}


def test_unique_transaction_identifier_amount_currency_and_date_can_link():
    result = document_matches(evidence(), [movement()])
    assert result[0]['automatic'] is True


def test_same_amount_and_date_alone_is_only_a_suggestion():
    assert document_matches(evidence(identifier=''), [movement()])[0]['automatic'] is False


def test_duplicate_or_internal_transfer_candidates_require_review():
    result = document_matches(evidence(), [movement(), movement(id='two', amount='100.00')])
    assert len(result) == 2 and not any(row['automatic'] for row in result)


def test_partial_payment_and_reversal_are_not_automatic():
    assert not document_matches(evidence(amount='50.00'), [movement()])[0]['automatic']
    assert not document_matches(evidence(), [movement(description='Estorno de pagamento')])[0]['automatic']


def test_currency_conflict_and_missing_date_are_not_matches():
    assert document_matches(evidence(), [movement(currency='USD')]) == []
    assert not any(row['automatic'] for row in document_matches(evidence(date=''), [movement()]))


def test_multiple_document_amounts_or_incomplete_search_cannot_auto_link():
    doc = evidence()
    doc['observations'].append({'amounts': {'value': '90.00'}})
    assert not document_matches(doc, [movement()])[0]['automatic']
    assert not document_matches(evidence(), [movement()], complete=False)[0]['automatic']


def test_native_receipt_identifier_and_brl_label_are_supported():
    doc = {'observations': [
        {'amounts': {'value': '100.00'}, 'attributes': {'value': 'Valor: R$ 100,00'}},
        {'dates': {'document_date': '2026-11-27'}},
        {'attributes': {'label': 'Identificação da operação', 'value': 'ID da transação: E1234567820261127012345678901234'}},
    ]}
    assert document_matches(doc, [movement()])[0]['automatic']
