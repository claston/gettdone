"""Paid OCR authorization, durable cost bounds and whole-document attempts."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from io import BytesIO

import pytest
from pypdf import PdfWriter

from app.application.access_control import AccessControlService
from app.application.asynchronous_textract import AsynchronousTextractReader
from app.application.conversion.identity import IdentityContext
from app.application.conversion.pro_conversion import (
    ConversionProAccessError,
    ConversionProQuota,
    ConversionProQuotaError,
    ProConversionPolicy,
)


@pytest.fixture
def quota(tmp_path, monkeypatch):
    service = AccessControlService(state_file=tmp_path / "state.json", token_secret="test-secret")
    monkeypatch.setattr(service, "_read_active_user_plan", lambda **kwargs: {"code": "profissional"})
    service.now_provider = lambda: datetime(2026, 10, 31, tzinfo=timezone.utc)
    identity = IdentityContext("user", "usr_paid", 300, quota_mode="pages", plan_code="profissional", max_pages_per_file_ocr=6)
    return ConversionProQuota(service, ProConversionPolicy(True, 6, 5)), identity


def test_free_and_disabled_modes_cannot_reserve_paid_calls(quota):
    budget, identity = quota
    with pytest.raises(ConversionProAccessError):
        budget.reserve("free", replace(identity, plan_code=None), 1)
    budget.policy = ProConversionPolicy(False, 6, 5)
    with pytest.raises(ConversionProAccessError):
        budget.reserve("disabled", identity, 1)


def test_reservations_are_idempotent_and_unattempted_pages_are_released(quota):
    budget, identity = quota
    assert budget.reserve("job_one", identity, 4) == 1
    assert budget.reserve("job_one", identity, 4) == 1
    budget.before_page("job_one", identity, 1)
    budget.finish("job_one", identity)
    assert budget.remaining(identity) == 4
    with pytest.raises(ConversionProAccessError):
        budget.reserve("job_one", identity, 4)


def test_owner_and_page_sequence_prevent_duplicate_paid_attempts(quota):
    budget, identity = quota
    budget.reserve("job_one", identity, 3)
    with pytest.raises(ConversionProAccessError):
        budget.before_page("job_one", replace(identity, identity_id="other"), 1)
    budget.before_page("job_one", identity, 1)
    with pytest.raises(ConversionProAccessError):
        budget.before_page("job_one", identity, 1)
    with pytest.raises(ConversionProAccessError):
        budget.before_page("job_one", identity, 3)


def test_concurrent_jobs_cannot_overrun_monthly_budget(quota):
    budget, identity = quota

    def reserve(index):
        try:
            budget.reserve(f"job_{index}", identity, 4)
            return True
        except ConversionProQuotaError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(reserve, [1, 2])) == [False, True]


def test_monthly_budget_resets_and_revoked_plan_is_blocked(quota, monkeypatch):
    budget, identity = quota
    budget.reserve("job_one", identity, 5)
    assert budget.remaining(identity) == 0
    budget.access.now_provider = lambda: datetime(2026, 11, 1, tzinfo=timezone.utc)
    assert budget.remaining(identity) == 5
    monkeypatch.setattr(budget.access, "_read_active_user_plan", lambda **kwargs: None)
    with pytest.raises(ConversionProAccessError):
        budget.reserve("job_two", identity, 1)


def pdf(pages):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=600, height=800)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def test_page_limit_is_checked_before_any_cloud_call():
    reader = AsynchronousTextractReader(bucket="test", s3_client=object(), textract_client=object())
    with pytest.raises(ValueError):
        reader.read(pdf(3), max_pages=2, request_token="a" * 64)


def test_definite_permission_rejection_releases_unprocessed_budget(quota):
    budget, identity = quota
    budget.reserve("job_denied", identity, 4)
    budget.before_page("job_denied", identity, 1)
    budget.reject_page("job_denied", identity, 1)
    budget.finish("job_denied", identity)
    assert budget.remaining(identity) == 5


def test_async_attempt_accounts_for_all_pages_atomically_and_rejects_reentry(quota):
    budget, identity = quota
    budget.reserve("async-job", identity, 4)
    with pytest.raises(ConversionProAccessError):
        budget.before_document("async-job", replace(identity, identity_id="other"), 4)
    with pytest.raises(ConversionProAccessError):
        budget.before_document("async-job", identity, 3)
    budget.before_document("async-job", identity, 4)
    with pytest.raises(ConversionProAccessError):
        budget.before_document("async-job", identity, 4)
    budget.finish("async-job", identity)
    assert budget.remaining(identity) == 1


def test_definite_async_start_rejection_releases_all_pages(quota):
    budget, identity = quota
    budget.reserve("async-rejected", identity, 4)
    budget.before_document("async-rejected", identity, 4)
    budget.reject_document("async-rejected", identity, 4)
    budget.finish("async-rejected", identity)
    assert budget.remaining(identity) == 5


def test_standard_text_and_analysis_dispatch_share_the_same_financial_policy(monkeypatch):
    from app.application import ocr_cash_statement, pdf_parser
    from app.application.pdf_layout_inference import PdfLayoutInference
    from tests.test_ocr_structure_experiment import evidence

    monkeypatch.setattr(
        ocr_cash_statement,
        "infer_pdf_layout",
        lambda text: PdfLayoutInference("asaas_extrato_conta_digital_movimentacoes_v1", 0.99, False),
    )
    text = pdf_parser._parse_textract_text_pages_impl(evidence(tables=False))
    analysis = pdf_parser._parse_textract_tables(evidence(tables=True), {"textract_mode": "analysis"})
    for result in (text, analysis):
        assert result.parse_metrics["selected_parser"] == "shared_ocr_cash_table"
        assert [row.amount for row in result.canonical_transactions] == [20, -5, -5]
        assert [row.source_page for row in result.canonical_transactions] == [1, 1, 1]
    assert text.transactions == analysis.transactions


@pytest.mark.parametrize("month,validated", [(2, True), (3, False)])
@pytest.mark.parametrize("missing_bank_header", [False, True])
def test_pro_reuses_shared_period_segmentation_and_preserves_page_provenance(month, validated, missing_bank_header, monkeypatch):
    import calendar

    from app.application import ocr_cash_statement
    from app.application.conversion import pro_pdf_parser
    from app.application.document_extraction_models import ExtractedLine
    from app.application.pdf_layout_inference import PdfLayoutInference
    from tests.test_ocr_structure_experiment import evidence

    first = evidence(tables=True)
    second = evidence(tables=True, page_number=2)

    def prepare(page, number):
        def text(value):
            value = value.replace("/01/2026", f"/{number:02}/2026")
            if number != 1:
                return "110,00" if value == "100,00" else "120,00" if value == "110,00" else value
            return value

        header = [
            ExtractedLine(
                f"{number}:bank",
                page.page_number,
                100,
                "EXTRATO DE CONTA CORRENTE" if missing_bank_header and number != 1 else "ASAAS EXTRATO DE CONTA CORRENTE",
            ),
            ExtractedLine(
                f"{number}:period",
                page.page_number,
                101,
                f"Período: 01/{number:02}/2026 a {calendar.monthrange(2026, number)[1]:02}/{number:02}/2026",
            ),
        ]
        return replace(
            page,
            words=[replace(word, text=text(word.text)) for word in page.words],
            lines=[*header, *[replace(line, text=text(line.text)) for line in page.lines]],
            tables=[
                replace(table, rows=[[replace(cell, text=text(cell.text)) for cell in row] for row in table.rows]) for table in page.tables
            ],
        )

    source = replace(first, pages=[prepare(first.pages[0], 1), prepare(second.pages[0], month)])
    monkeypatch.setattr(
        ocr_cash_statement,
        "infer_pdf_layout",
        lambda text: PdfLayoutInference("asaas_extrato_conta_digital_movimentacoes_v1" if "ASAAS" in text else "unknown", 0.99, False),
    )
    result = pro_pdf_parser.project_pro_extraction(source)
    assert len(result.transactions) == 6
    assert result.parse_metrics["statement_blocks_count"] == 2
    assert bool(result.parse_metrics["financial_controls_validated"]) is validated
    assert [row.source_page for row in result.canonical_transactions] == [1, 1, 1, 2, 2, 2]
    assert all(region.page == row.source_page for row in result.canonical_transactions for region in row.source_regions)
