from copy import deepcopy
from datetime import date

from app.application.statement_work_assessment import assess_work, coverage


def sample():
    client = {"id": "c", "tax_id": "12345678901"}
    period = {"id": "p", "start_date": "2026-09-20", "end_date": "2026-10-19"}
    account = {"id": "a", "client_id": "c", "bank_code": "033", "branch": "0001", "account_number": "00123-4"}
    doc = {
        "job_id": "job_1",
        "batch_id": "batch_1",
        "account_id": "a",
        "filename": "extrato.pdf",
        "status": "completed",
        "expired": False,
        "context_version": 1,
        "reviewed_at": "now",
        "source": {},
        "analysis": {
            "updated_at": "v1",
            "preview_transactions": [
                {"transaction_id": "before", "date": "2026-09-19", "amount": 100, "description": "Fora"},
                {"transaction_id": "inside", "date": "2026-09-20", "amount": 12, "description": "PIX"},
            ],
        },
    }
    settings = {
        "account_ids": ["a"],
        "scope_confirmed_at": "now",
        "evidence": {"job_1": {"identity_confirmed": True, "start_date": "2026-09-20", "end_date": "2026-10-19"}},
        "exclusions": {},
        "duplicate_resolutions": {},
    }
    return client, period, [account], [doc], settings


def test_coverage_uses_document_evidence_and_transactions_are_cut_without_copying():
    args = sample()
    assessment = assess_work(*args)
    assert assessment["coverage"][0]["status"] == "complete"
    assert [row["transaction_id"] for row in assessment["movements"]] == ["inside"]
    assert not assessment["blockers"]
    args[-1]["evidence"]["job_1"].pop("start_date")
    args[-1]["evidence"]["job_1"].pop("end_date")
    assessment = assess_work(*args)
    assert assessment["coverage"][0]["status"] == "indeterminate"
    assert any(item["code"] == "coverage_indeterminate" for item in assessment["reservations"])


def test_scope_empty_unreviewed_financial_conflicts_and_exclusion():
    args = sample()
    args[-1]["scope_confirmed_at"] = None
    assert "scope_unconfirmed" in {item["code"] for item in assess_work(*args)["blockers"]}
    args[-1]["scope_confirmed_at"] = "now"
    args[3][0]["analysis"]["statement_balance_check"] = {"difference": 1}
    args[3][0]["reviewed_at"] = "reviewed"
    assert "financial_conflict" in {item["code"] for item in assess_work(*args)["blockers"]}
    args[-1]["exclusions"]["job_1"] = {"reason": "Dados inconsistentes excluídos", "actor": "owner", "at": "now"}
    assert "no_valid_statement" in {item["code"] for item in assess_work(*args)["blockers"]}


def test_overlaps_duplicate_groups_and_resolution_are_explicit():
    args = sample()
    other = deepcopy(args[3][0])
    other["job_id"] = "job_2"
    args[3].append(other)
    args[-1]["evidence"]["job_2"] = deepcopy(args[-1]["evidence"]["job_1"])
    assessment = assess_work(*args)
    assert assessment["coverage"][0]["status"] == "overlapping"
    assert "possible_duplicate" in {item["code"] for item in assessment["blockers"]}
    group = assessment["duplicate_groups"][0]
    args[-1]["duplicate_resolutions"][group["id"]] = {"reason": "Conferidos no PDF: movimentos distintos", "actor": "owner", "at": "now"}
    assert "possible_duplicate" not in {item["code"] for item in assess_work(*args)["blockers"]}


def test_empty_statement_requires_explicit_confirmation_and_expired_data_is_not_returned():
    args = sample()
    args[3][0]["analysis"] = {
        "preview_transactions": [],
        "opening_balance": 100,
        "closing_balance": 100,
        "pdf_processing_metrics": {"statement_no_activity": 1},
    }
    assert "empty_unconfirmed" in {item["code"] for item in assess_work(*args)["blockers"]}
    args[-1]["evidence"]["job_1"]["empty_confirmed"] = True
    assert not assess_work(*args)["blockers"]
    args[3][0]["expired"] = True
    assessment = assess_work(*args)
    assert not assessment["movements"]
    assert "document_unavailable" in {item["code"] for item in assessment["blockers"]}


def test_adjacent_documentary_intervals_and_date_limits_are_safe():
    assert (
        coverage(
            date(2026, 9, 20),
            date(2026, 10, 19),
            [
                (date(2026, 9, 20), date(2026, 9, 30)),
                (date(2026, 10, 1), date(2026, 10, 19)),
            ],
            True,
        )["status"]
        == "complete"
    )
    assert coverage(date(9999, 12, 1), date.max, [(date(9999, 12, 1), date.max)], True)["status"] == "complete"


def test_transfers_and_reversals_are_not_duplicates_or_revenue_categories():
    args = sample()
    args[2].append({**args[2][0], "id": "b"})
    args[-1]["account_ids"].append("b")
    args[3][0]["analysis"]["preview_transactions"] = [
        {"transaction_id": "transfer-out", "date": "2026-10-01", "amount": -12, "description": "Transferência"},
        {"transaction_id": "reversal", "date": "2026-10-01", "amount": 12, "description": "Estorno transferência"},
    ]
    other = deepcopy(args[3][0])
    other.update(job_id="job_2", account_id="b")
    other["analysis"]["preview_transactions"] = [
        {"transaction_id": "transfer-in", "date": "2026-10-01", "amount": 12, "description": "Transferência"}
    ]
    args[3].append(other)
    args[-1]["evidence"]["job_2"] = deepcopy(args[-1]["evidence"]["job_1"])
    result = assess_work(*args)
    assert not result["duplicate_groups"] and len(result["movements"]) == 3
    assert "revenue" not in result and "expenses" not in result
    args[3][0]["source"] = {"holder_tax_id": "99999999999"}
    assert "identity_conflict" in {i["code"] for i in assess_work(*args)["blockers"]}


def test_outside_period_only_does_not_allow_concluding_an_empty_work():
    args = sample()
    args[-1]["evidence"]["job_1"] = {"identity_confirmed": True}
    args[3][0]["analysis"]["preview_transactions"] = [{"date": "2026-09-01", "description": "Fora", "amount": 1}]
    assert "no_valid_statement" in {i["code"] for i in assess_work(*args)["blockers"]}
