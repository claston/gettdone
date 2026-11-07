import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which('node') is None, reason='Node is required for frontend regressions')
def test_position_count_and_unknown_printed_balance_are_not_cash_or_zero():
    source = (Path(__file__).resolve().parents[2] / 'frontend/ofx-convert.js').read_text(encoding='utf-8')
    functions = []
    for name in ['renderFinancialDocumentKpis', 'financialObservationValue']:
        match = re.search(r'(?m)^  function '+name+r'\(', source)
        assert match is not None, name
        end = source.index('\n  }', match.end())+len('\n  }')
        functions.append(source[match.start():end])
    program = 'const FUNCTIONS = '+json.dumps('\n'.join(functions))+';\n'+r'''
const assert = require('node:assert/strict');
const vm = require('node:vm');
const context = { kpis: {}, state: {}, bankCodeOptions: [], financialDocument: a => a.financial_document,
  escapeAttr: s => String(s).replace(/</g, '&lt;'), formatCurrency: n => `BRL ${n}` };
vm.createContext(context); vm.runInContext(FUNCTIONS, context);
const observations = [
  {observation_type: 'investment_section_total', amounts: {closing_gross: '440.00', closing_net: '432.00'}},
  {observation_type: 'cash_position', amounts: {}, attributes: {printed_value: '-'}},
];
context.renderFinancialDocumentKpis({financial_document: {document_type: 'investment_statement',
  events: [], observations, validations: [], metadata: {product: 'CDB', position_count: 4}, notice: 'Posições de CDB',
  evidence: ['btg_cdb_positions_and_details'], export: {requires_destination_account: false, available_formats: ['csv', 'xlsx']}}});
assert.match(context.kpis.innerHTML, /Posições de CDB/);
assert.match(context.kpis.innerHTML, /kpi-value">4</);
assert.match(context.kpis.innerHTML, /BRL 440/);
assert.match(context.kpis.innerHTML, /BRL 432/);
assert.equal(context.financialObservationValue(observations[1]), '-');
assert.equal(context.financialObservationValue({amounts:{}}), 'Não informado');
assert.equal(context.financialObservationValue({amounts:{net:'0.00'}}), 'BRL 0');
'''
    result = subprocess.run([shutil.which('node'), '-'], input=program, text=True, encoding='utf-8', capture_output=True, timeout=20)
    assert result.returncode == 0, result.stdout+result.stderr
