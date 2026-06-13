import json

from app.application.models import AnalysisData, BeforeAfterRow, TransactionRow
from app.application.storage_service import TempAnalysisStorage
from app.application.structured_conversion import (
    STRUCTURED_CONVERSION_CONTRACT_VERSION,
    build_structured_conversion_result_from_analysis_data,
)


def _build_analysis_data() -> AnalysisData:
    return AnalysisData(
        analysis_id="an_structured123",
        file_type="pdf",
        upload_filename="extrato.pdf",
        semantic_type="bank_statement",
        semantic_confidence=0.97,
        semantic_evidence=["pdf", "statement"],
        transactions_total=2,
        total_inflows=120.5,
        total_outflows=-20.0,
        net_total=100.5,
        preview_transactions=[
            TransactionRow(
                date="2026-04-01",
                description="SALARIO",
                amount=120.5,
                category="Outros",
                reconciliation_status="unmatched",
                running_balance=120.5,
            ),
            TransactionRow(
                date="2026-04-02",
                description="TARIFA",
                amount=-20.0,
                category="Outros",
                reconciliation_status="unmatched",
                warning_types=["layout_fallback"],
            ),
        ],
        report_transactions=[
            TransactionRow(
                date="2026-04-01",
                description="SALARIO",
                amount=120.5,
                category="Outros",
                reconciliation_status="unmatched",
                running_balance=120.5,
            ),
            TransactionRow(
                date="2026-04-02",
                description="TARIFA",
                amount=-20.0,
                category="Outros",
                reconciliation_status="unmatched",
                warning_types=["layout_fallback"],
            ),
        ],
        preview_before_after=[
            BeforeAfterRow(
                date="2026-04-02",
                description_before="tarifa",
                description_after="TARIFA",
                amount_before=-20.0,
                amount_after=-20.0,
            )
        ],
        matched_groups=1,
        reversed_entries=0,
        potential_duplicates=0,
        updated_at="2026-06-13T18:40:00+00:00",
        layout_inference_name="generic_statement_ptbr",
        layout_inference_confidence=0.61,
        pdf_processing_metrics={
            "selected_parser": "grouped",
            "export_recommendation": "review_recommended",
            "export_recommendation_reason": "low_confidence_band",
        },
        ofx_account_type="bank",
        opening_balance=10.0,
        closing_balance=110.5,
        bank_name="Itau",
        bank_branch="1234",
        account_number="998877",
        bank_code="341",
    )


def test_build_structured_conversion_result_from_analysis_data() -> None:
    structured = build_structured_conversion_result_from_analysis_data(
        _build_analysis_data(),
        expires_at="2026-06-14T18:40:00+00:00",
    )

    assert structured.contract_version == STRUCTURED_CONVERSION_CONTRACT_VERSION
    assert structured.conversion_id == "an_structured123"
    assert structured.source.filename == "extrato.pdf"
    assert structured.document.semantic_type == "bank_statement"
    assert structured.document.bank_name == "Itau"
    assert structured.account.account_type == "bank"
    assert structured.balances.closing_balance == 110.5
    assert structured.summary.transactions_total == 2
    assert structured.transactions[0].transaction_id == "txn_0001"
    assert structured.transactions[0].transaction_type == "inflow"
    assert structured.transactions[1].transaction_type == "outflow"
    assert structured.transactions[1].warning_types == ["layout_fallback"]
    assert structured.export.recommended_review is True
    assert structured.export.recommendation_code == "review_recommended"
    assert structured.timestamps.expires_at == "2026-06-14T18:40:00+00:00"


def test_save_analysis_persists_structured_result(tmp_path) -> None:
    storage = TempAnalysisStorage(root_dir=tmp_path, ttl_seconds=3600)
    storage.save_analysis(_build_analysis_data())

    payload = json.loads((tmp_path / "an_structured123" / "analysis.json").read_text(encoding="utf-8"))

    assert payload["structured_result"]["contract_version"] == STRUCTURED_CONVERSION_CONTRACT_VERSION
    assert payload["structured_result"]["analysis_id"] == "an_structured123"
    assert payload["structured_result"]["document"]["semantic_type"] == "bank_statement"
    assert payload["structured_result"]["account"]["bank_code"] == "341"
    assert payload["structured_result"]["transactions"][1]["transaction_type"] == "outflow"
    assert payload["structured_result"]["export"]["recommended_review"] is True
