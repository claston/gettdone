"""Execute the real browser functions: unknown balances must stay unknown."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="Node is required to execute frontend balance regressions")
def test_conversion_edit_render_and_download_keep_unavailable_balances_unknown():
    source = (Path(__file__).resolve().parents[2] / "frontend" / "ofx-convert.js").read_text(encoding="utf-8")
    names = ["isNoActivityStatement", "renderKpis", "applyConversionPayload", "buildDisplayedRunningBalances",
             "buildDerivedSummaryFromRows", "syncOpeningBalanceFromPayload", "syncClosingBalanceFromPayload", "runDownloadReport"]
    if "function hasUnavailableBalanceBounds(" in source:
        names.append("hasUnavailableBalanceBounds")
    functions = []
    for name in names:
        match = re.search(r"(?m)^  (?:async )?function " + name + r"\(", source)
        assert match is not None, name
        end = source.index("\n  }", match.end()) + len("\n  }")
        functions.append(source[match.start():end])
    program = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const noop = () => {};
const state = { previewRows: [], bankCodeOverride: '341' };
let sentSettings;
const context = {
  state, URLSearchParams, outputFormat: 'ofx', kpis: {}, bankCodeOptions: [], apiBase: 'http://local',
  hasStatementAccounts: () => false, financialDocument: () => null, isPayablesReport: () => false,
  resolveConvertedPages: () => 1, buildReviewWarningsMarkup: () => '', formatCurrency: n => `BRL ${n}`,
  toMoneyInputValue: String, resolveOfxAccountType: () => 'bank_statement',
  normalizeBankBranchDisplay: String, normalizeAccountDisplay: String, normalizeQuotaMode: String,
  resolveInitialBankCode: () => '341', markChangedRow: noop, addRowBtn: null,
  setPreviewRows: rows => { state.previewRows = rows; }, setOriginalRows: noop, renderRows: noop,
  analysisIdNode: null, processingIdNode: null, updateQuotaRemainingValue: noop,
  reviewSection: { classList: { remove: noop } }, downloadSection: { classList: { remove: noop } },
  setDownloadButtonsDisabled: noop, persistCurrentViewState: noop, showConversionFeedback: noop,
  isOpeningBalanceRow: () => false, roundMoney: n => Math.round(n * 100) / 100,
  getRunningBalanceAmount: row => row.running_balance == null ? null : Number(row.running_balance),
  resolveDownloadConfig: () => ({ targetId: 'an_test', reportFormat: 'ofx' }), hasUserSession: () => true,
  buildIdentityQueryParams: () => new URLSearchParams(), setStatus: noop,
  normalizeDigits: value => String(value || '').replace(/\D/g, ''),
  session: { request: async (_, options) => {
    sentSettings = JSON.parse(options.body);
    throw new Error('Stop after capturing the actual save-before-download request');
  } },
};
vm.createContext(context);
vm.runInContext(FUNCTIONS, context);
const analysis = { analysis_id: 'an_test', opening_balance: null, closing_balance: null,
  net_total: 50, total_inflows: 100, total_outflows: -50,
  preview_transactions: [{ date: '2026-10-01', amount: -50 }, { date: '2026-10-01', amount: 100 }],
  pdf_processing_metrics: { balance_bounds_unavailable: 1 } };
(async () => {
  context.applyConversionPayload({ analysis });
  assert.equal(state.openingBalanceOverride, null);
  assert.equal(state.closingBalanceOverride, null);
  assert.match(context.kpis.innerHTML, /id="opening-balance-input"[^>]*value=""/);
  assert.match(context.kpis.innerHTML, /id="closing-balance-input"[^>]*value=""/);
  assert.equal(JSON.stringify(context.buildDisplayedRunningBalances(analysis.preview_transactions)), '[null,null]');
  assert.equal(JSON.stringify(context.buildDisplayedRunningBalances([{ amount: 1, running_balance: 20 }, { amount: 2 }])), '[20,null]');
  state.openingBalanceOverride = 0;
  state.closingBalanceOverride = 50;
  context.syncOpeningBalanceFromPayload({ opening_balance: null });
  context.syncClosingBalanceFromPayload({ closing_balance: null, net_total: 50 });
  assert.equal(state.openingBalanceOverride, null);
  assert.equal(state.closingBalanceOverride, null);
  context.outputFormat = 'csv';
  context.renderKpis(analysis);
  assert.equal((context.kpis.innerHTML.match(/<p class="kpi-value">Não informado<\/p>/g) || []).length, 2);
  state.openingBalanceOverride = 0; // A stale restored view must not send invented bounds.
  state.closingBalanceOverride = 50;
  await context.runDownloadReport('ofx');
  assert.ok(sentSettings);
  assert.equal(sentSettings.opening_balance, null);
  assert.equal(sentSettings.closing_balance, null);
  const known = { ...analysis, opening_balance: 0, closing_balance: 50, pdf_processing_metrics: {} };
  context.applyConversionPayload({ analysis: known });
  assert.equal(state.openingBalanceOverride, 0); // A printed zero is not a missing balance.
  assert.equal(state.closingBalanceOverride, 50);
  assert.equal(JSON.stringify(context.buildDisplayedRunningBalances(known.preview_transactions)), '[-50,50]');
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    program = "const FUNCTIONS = " + json.dumps("\n".join(functions)) + ";\n" + program
    result = subprocess.run([NODE, "-"], input=program, text=True, encoding="utf-8", capture_output=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
