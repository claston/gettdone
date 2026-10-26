from copy import deepcopy

from app.application.models import CanonicalTransaction
from app.application.normalization.balance import annotate_balance_consistency


def row(date, amount, balance, **kwargs):
    return CanonicalTransaction(date, "LANÇAMENTO SINTÉTICO", amount, "inflow" if amount > 0 else "outflow",
                                running_balance=balance, **kwargs)


def test_balance_warning_explains_reference_movement_expected_reported_and_difference():
    rows = [row("2026-08-01", 100, 1000, source_page=1, source_line=10), row("2026-08-02", -100, 880)]
    assert annotate_balance_consistency(rows) == (1, 1)
    check = getattr(rows[1], "balance_check", None)
    assert check is not None
    assert (check.reference_balance, check.credits_total, check.debits_total) == (1000, 0, 100)
    assert (check.expected_balance, check.reported_balance, check.difference) == (900, 880, -20)
    assert (check.reference_date, check.reference_page, check.reference_line) == ("2026-08-01", 1, 10)
    assert check.order == "ascending" and check.movements_count == 1


def test_descending_and_sparse_balance_checks_use_the_actual_validator_equation():
    layout = "santander_aplicativo_empresas_conta_corrente_extrato_v1"
    rows = [row("2026-08-03", -100, 880, layout_name=layout),
            row("2026-08-02", 20, None, layout_name=layout),
            row("2026-08-01", 50, 1000, layout_name=layout)]
    assert annotate_balance_consistency(rows) == (1, 1)
    check = getattr(rows[2], "balance_check", None)
    assert check is not None
    assert (check.expected_balance, check.reported_balance, check.difference) == (960, 1000, 40)
    assert (check.credits_total, check.debits_total, check.movements_count) == (20, 100, 2)
    assert check.order == "descending"


def test_checkpoint_warning_uses_the_checkpoint_not_the_previous_statement():
    rows = [row("2026-08-01", 10, 100), row("2026-08-02", 20, 1025)]
    assert annotate_balance_consistency(rows, balance_checkpoints={1: 1000}) == (1, 1)
    check = getattr(rows[1], "balance_check", None)
    assert check is not None
    assert (check.reference_balance, check.expected_balance, check.difference) == (1000, 1020, 5)
    assert check.reference_date is None


def test_legacy_btg_details_are_read_only_and_follow_accounting_not_display_order():
    from app.application.balance_checks import enrich_legacy_balance_checks

    analysis = {"layout_inference_name": "btg_pactual_conta_corrente_pj_v1", "opening_balance": 900,
                "closing_balance": 880, "total_inflows": 100, "total_outflows": -100,
                "preview_transactions": [
                    {"date": "2026-08-01", "amount": 100, "running_balance": 1000, "source_sequence": 2},
                    {"date": "2026-08-02", "amount": -100, "running_balance": 880, "source_sequence": 1,
                     "warning_types": ["balance_consistency_failed", "printed_summary_balance_mismatch"]}]}
    original = deepcopy(analysis)
    enriched = enrich_legacy_balance_checks(analysis)
    assert analysis == original
    assert enriched["preview_transactions"][1]["balance_check"]["expected_balance"] == 900
    assert enriched["statement_balance_check"]["difference"] == -20
    assert enrich_legacy_balance_checks({**analysis, "layout_inference_name": "unknown"}) == {
        **analysis, "layout_inference_name": "unknown"}


def test_pipeline_storage_api_and_edits_preserve_the_recorded_comparison(tmp_path):
    from dataclasses import asdict, replace

    from app.application.conversion.result_payload import build_analyze_response, persist_conversion_result
    from app.application.conversion.uploaded_document import ingest_uploaded_document
    from app.application.conversion_pipeline import ConversionPipeline
    from app.application.parsers.service import ParsingService
    from app.application.storage_service import TempAnalysisStorage

    document = ingest_uploaded_document("synthetic.csv", b"date,description,amount\n2026-08-01,ENTRADA,100\n2026-08-02,SAIDA,-100\n")
    canonical = [row("2026-08-01", 100, 1000), row("2026-08-02", -100, 880)]
    annotate_balance_consistency(canonical)
    canonical[-1].warnings.append("printed_summary_balance_mismatch")
    parsed = replace(ParsingService().parse(document), canonical_transactions=canonical,
        warning_types=[item.warnings for item in canonical], running_balances=[1000, 880],
        parse_metrics={"printed_opening_balance": 900, "printed_closing_balance": 880})
    result = ConversionPipeline().run_parsed_document(document=document, parsed_document=parsed,
                                                      analysis_id="an_balance_details", parse_ms=1)
    storage = TempAnalysisStorage(root_dir=tmp_path)
    persisted = persist_conversion_result(storage=storage, pipeline_result=result)
    response = build_analyze_response(persisted_result=persisted).model_dump(mode="json")
    recorded = response["preview_transactions"][1]["balance_check"]
    assert recorded == asdict(canonical[1].balance_check)
    assert response["statement_balance_check"]["difference"] == -20
    changed = storage.apply_convert_edits("an_balance_details", [{
        "row_id": response["preview_transactions"][1]["transaction_id"], "date": "2026-08-02",
        "description": "CORRIGIDO", "debit": 99, "balance_check": {"difference": 0}}])
    assert changed["preview_transactions"][1]["balance_check"] == recorded
    assert changed["statement_balance_check"] == response["statement_balance_check"]


def test_invalid_and_absent_optional_comparisons_remain_compatible():
    from app.application.balance_checks import parse_balance_check

    assert parse_balance_check(None) is None
    assert parse_balance_check({"difference": float("nan")}) is None


def test_legacy_first_transaction_uses_the_explicit_opening_balance():
    from app.application.balance_checks import enrich_legacy_balance_checks

    analysis = {"layout_inference_name": "btg_pactual_conta_corrente_pj_v1", "opening_balance": 1000,
        "preview_transactions": [{"date": "2026-08-01", "amount": -100, "running_balance": 890,
                                  "warning_types": ["balance_consistency_failed"]}]}
    check = enrich_legacy_balance_checks(analysis)["preview_transactions"][0]["balance_check"]
    assert (check["reference_balance"], check["expected_balance"], check["difference"]) == (1000, 900, -10)
