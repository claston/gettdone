import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location('backfill', Path(__file__).parents[2] / 'scripts/backfill_document_retention.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_backfill_distinguishes_paid_inputs_results_and_temporary_free_files():
    paid = 'doc_' + 'a' * 24
    free = 'doc_' + 'b' * 24
    def kind(key):
        return module.classify_key(key, {paid}, {'an_paid'}, 'conversion/jobs', 'conversion/results')
    assert kind(f'conversion/jobs/{paid}/document.pdf') == 'paid'
    assert kind(f'conversion/jobs/{free}/document.csv') == 'temporary'
    assert kind('conversion/results/an_paid/analysis.json') == 'paid'
    assert kind('conversion/results/an_free/analysis.json') == 'temporary'
    assert kind('conversion/results/canonical/an_paid/v1/export/converted.csv') == 'paid'
    assert kind('conversion/jobs/pro-fiscal/source/document.xml') == 'paid'
    assert kind('unrelated/file') is None
