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
    for name in ['renderFinancialDocumentKpis', 'financialObservationValue', 'financialObservationDate', 'financialObservationLabel']:
        match = re.search(r'(?m)^  function '+name+r'\(', source)
        assert match is not None, name
        end = source.index('\n  }', match.end())+len('\n  }')
        functions.append(source[match.start():end])
    program = 'const FUNCTIONS = '+json.dumps('\n'.join(functions))+';\n'+r'''
const assert = require('node:assert/strict');
const vm = require('node:vm');
const context = { kpis: {}, state: {}, bankCodeOptions: [], financialDocument: a => a.financial_document,
  escapeAttr: s => String(s).replace(/</g, '&lt;'), formatCurrency: n => `BRL ${n}`, formatDate: d => d };
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
const monthly = {observation_type:'investment_monthly_summary', dates:{},
  amounts:{applications:'100.00',redemptions:'50.00'}, attributes:{statement_month:'10/2026',product:'SICREDINVEST',label:'Resumo mensal'}};
assert.equal(context.financialObservationDate(monthly), '10/2026');
assert.equal(context.financialObservationLabel(monthly), 'SICREDINVEST — Resumo mensal');
assert.match(context.financialObservationValue(monthly), /Aplicações: BRL 100; Resgates: BRL 50/);
context.renderFinancialDocumentKpis({financial_document: {document_type:'investment_statement',events:[],
  observations:[monthly],validations:[],metadata:{summary_scope:'per_block'},notice:'Posições',
  export:{requires_destination_account:false,available_formats:['csv','xlsx']}}});
assert.match(context.kpis.innerHTML, /Por investimento e período/);
assert.doesNotMatch(context.kpis.innerHTML, /Saldo bruto final|Saldo líquido final/);
context.renderFinancialDocumentKpis({total_inflows:0,total_outflows:0,financial_document:{document_type:'acquirer_sales_report',
  events:[{amount:'50.00',cash_effect:null}],observations:[],validations:[],metadata:{summary_scope:'per_block'},
  evidence:['stone_payment_receipt_rows'],notice:'Repasses',export:{requires_destination_account:false,available_formats:['csv','xlsx']}}});
assert.match(context.kpis.innerHTML, /Valor dos repasses listados/);
assert.match(context.kpis.innerHTML, /BRL 50/);
assert.doesNotMatch(context.kpis.innerHTML, /Recebimentos \/ resgates/);
'''
    result = subprocess.run([shutil.which('node'), '-'], input=program, text=True, encoding='utf-8', capture_output=True, timeout=20)
    assert result.returncode == 0, result.stdout+result.stderr
