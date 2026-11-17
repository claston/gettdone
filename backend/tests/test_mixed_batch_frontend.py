"""Run batch rendering and retry with synthetic jobs, without a browser dependency."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
SOURCE = Path(__file__).resolve().parents[2] / "frontend" / "ofx-convert.js"


@pytest.mark.skipif(NODE is None, reason="Node is required for frontend regressions")
@pytest.mark.parametrize("scenario", [
    "initial_failure", "retry", "mixed_feedback", "next_upload_failure", "next_submit_failure", "next_poll_failure",
    "next_results_loaded",
])
def test_batch_failure_and_retry(scenario):
    source = SOURCE.read_text(encoding="utf-8")
    functions = []
    for name in ("isFiscalBatchJob", "batchFailureMessage", "retryBatchJob", "renderBatchResults", "runAsyncBatchConvert"):
        match = re.search(r"(?m)^  (?:async )?function " + name + r"\(", source)
        assert match is not None
        end = source.index("\n  }", match.end()) + len("\n  }")
        functions.append(source[match.start():end])
    setup = r"""
const assert = require('node:assert/strict');
class Element {
  constructor() { this.children = []; this.events = {}; this.classes = new Set(); }
  set innerHTML(value) { this.children = []; }
  appendChild(child) { this.children.push(child); }
  addEventListener(name, callback) { this.events[name] = callback; }
  classList = {add: value => this.classes.add(value), remove: value => this.classes.delete(value)};
}
const document = { createElement: () => new Element() };
const batchResultsPanel = new Element();
const state = { isLoading: false, activeBatchId: 'batch-test', batchResults: [] };
const setLoading = value => { state.isLoading = value; };
const setStatus = () => {};
const setProgressTarget = () => {};
const showProgressBar = () => {};
const startProgressDrift = () => {};
const hasUserSession = () => false;
const ensureAnonymousSession = async () => {};
const sha256File = async () => 'a'.repeat(64);
let nextBatchId = 'batch-test';
let failAt = null;
const uploadPreparedBatchFiles = async () => {
  if (failAt === 'upload') throw new Error('Synthetic upload failure');
};
const window = { crypto: { randomUUID: () => 'test-request' } };
let opened = [];
const applyConversionPayload = value => opened.push(value);
const reviewSection = { scrollIntoView: () => {} };
const failed = { job_id: 'failed-id', filename: 'scan.pdf', status: 'failed',
  error_code: 'OcrFinancialAmbiguityError', error_message: 'Confira as páginas 2, 3.' };
const ready = { ...failed, status: 'completed', result: {analysis: {id: 'result'}} };
const requests = [];
let polled = { status: 'failed', jobs: [failed] };
const pollConversionBatch = async id => {
  assert.ok(['batch-test', 'second-batch'].includes(id));
  if (failAt === 'poll' && id === 'second-batch') throw new Error('Synthetic polling failure');
  return polled;
};
const fetchBatchJson = async (path, init) => {
  requests.push({path, init});
  if (failAt === 'submit' && path.endsWith('/submit')) throw new Error('Synthetic submit failure');
  return {batch_id: nextBatchId, jobs: polled.jobs, uploads: []};
};
const rows = () => batchResultsPanel.children[1].children;
"""
    scenarios = {
        "initial_failure": """
await runAsyncBatchConvert([{name: 'scan.pdf', type: 'application/pdf', size: 100}]);
assert.equal(state.isLoading, false);
assert.equal(rows()[0].children[1].disabled, false, 'Retry must be enabled after initial processing ends');
assert.equal(rows()[0].children[0].children[1].textContent, 'Confira as páginas 2, 3.');
""",
        "retry": """
renderBatchResults([failed]);
polled = {status: 'completed', jobs: [ready]};
await retryBatchJob(failed);
assert.equal(requests.length, 1);
assert.equal(requests[0].path, '/api/conversion-batches/batch-test/jobs/failed-id/retry');
assert.equal(requests[0].init.method, 'POST');
assert.equal(opened.length, 0, 'Retry must preserve the open review');
assert.equal(batchResultsPanel.classes.has('hidden'), false, 'Single retried result must remain accessible');
assert.equal(rows()[0].children[1].textContent, 'Revisar');
rows()[0].children[1].events.click();
assert.deepEqual(opened, [ready.result]);
""",
        "mixed_feedback": """
renderBatchResults([ready, {...failed, error_code: 'fiscal_invoice'},
  {...failed, error_code: 'InternalError', error_message: 'SECRET diagnostic'}]);
assert.equal(rows()[0].children[1].textContent, 'Revisar');
assert.equal(rows()[1].children.length, 1, 'Fiscal notes must not offer bank retry');
assert.match(rows()[1].children[0].children[0].textContent, /Nota fiscal identificada/);
assert.doesNotMatch(rows()[2].children[0].children[1].textContent, /SECRET/);
assert.equal(isFiscalBatchJob({...failed, error_code: 'UnsupportedDocumentContentError',
  error_message: 'Este arquivo é uma nota fiscal eletrônica (NF-e ou NFS-e), não um extrato.'}), true);
""",
        "next_results_loaded": """
await runAsyncBatchConvert([{name: 'first.pdf', type: 'application/pdf', size: 100}]);
nextBatchId = 'second-batch';
polled = {status: 'failed', jobs: [{...failed, job_id: 'second-job', filename: 'second.pdf'}]};
await runAsyncBatchConvert([{name: 'second.pdf', type: 'application/pdf', size: 100}]);
assert.equal(state.activeBatchId, 'second-batch');
assert.equal(state.batchResults[0].job_id, 'second-job');
assert.equal(rows()[0].children[1].disabled, false);
await retryBatchJob(state.batchResults[0]);
assert.equal(requests.find(item => item.path.endsWith('/retry')).path,
  '/api/conversion-batches/second-batch/jobs/second-job/retry');
""",
    }
    failure_scenario = """
await runAsyncBatchConvert([{name: 'first.pdf', type: 'application/pdf', size: 100}]);
nextBatchId = 'second-batch';
failAt = 'FAILURE_STAGE';
const pending = runAsyncBatchConvert([{name: 'second.pdf', type: 'application/pdf', size: 100}]);
assert.equal(rows()[0].children[1].disabled, true, 'Disable retry while another batch is running');
await pending;
assert.equal(state.activeBatchId, 'batch-test', 'Displayed jobs must keep their original batch after another upload fails');
assert.equal(state.batchResults[0].job_id, 'failed-id');
assert.equal(rows()[0].children[1].disabled, false);
await retryBatchJob(state.batchResults[0]);
assert.equal(requests.find(item => item.path.endsWith('/retry')).path,
  '/api/conversion-batches/batch-test/jobs/failed-id/retry');
"""
    scenarios.update({f"next_{stage}_failure": failure_scenario.replace("FAILURE_STAGE", stage)
                      for stage in ("upload", "submit", "poll")})
    program = (setup + "\n".join(functions) + "\n(async () => {" + scenarios[scenario]
               + "})().catch(e => {console.error(e); process.exitCode = 1;});")
    result = subprocess.run([NODE, "-"], input=program, text=True, encoding="utf-8", capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
