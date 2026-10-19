"""Reviewer rectangles are editable metadata, independent of extraction/finance."""
import json

import pytest

from app.application.models import AnalysisData, TransactionRow
from app.application.source_regions import SourceRegion
from app.application.storage_service import TempAnalysisStorage
from app.schemas import ConvertEditPatch

BOX = {"page": 1, "left": 0.1, "top": 0.3, "width": 0.6, "height": 0.025}


@pytest.fixture
def saved(tmp_path):
    storage = TempAnalysisStorage(root_dir=tmp_path)
    row = TransactionRow("2026-04-01", "ORIGINAL", 10, "Outros", "unmatched",
                         transaction_id="stable", source_sequence=1,
                         source_regions=[SourceRegion(**{**BOX, "top": 0.1})], running_balance=110)
    storage.save_analysis(AnalysisData("an_marks", "pdf", "original.pdf", 1, 10, 0, 10,
                                      [row], [row], pdf_processing_metrics={"page_count": 2}))
    return storage, tmp_path / "an_marks" / "analysis.json"


def test_mark_clear_reopen_and_idempotency_do_not_change_finances_or_original_evidence(saved, monkeypatch):
    storage, path = saved
    original = json.loads(path.read_text(encoding="utf-8"))
    # Annotation-only updates may not recalculate balances or rewrite exports.
    monkeypatch.setattr(storage, "_recalculate_running_balances", lambda **kw: pytest.fail("financial recalculation"))
    monkeypatch.setattr(storage, "_write_report_workbook", lambda *a, **kw: pytest.fail("report rewrite"))
    patch = {"row_id": "stable", "action": "annotate", "review_regions": [BOX]}
    result = storage.apply_convert_edits("an_marks", [patch], operation_id="draw-1")
    assert result["preview_transactions"][0]["review_regions"] == [BOX]
    assert storage.apply_convert_edits("an_marks", [patch], operation_id="draw-1") == result
    reloaded = TempAnalysisStorage(root_dir=path.parent.parent).get_convert_edit_operation("an_marks", "draw-1")
    assert reloaded["preview_transactions"][0]["review_regions"] == [BOX]
    now = json.loads(path.read_text(encoding="utf-8"))
    for name in ("preview_transactions", "report_transactions"):
        assert now[name][0]["source_regions"] == original[name][0]["source_regions"]
        for key in ("date", "description", "amount", "running_balance", "is_deleted"):
            assert now[name][0][key] == original[name][0][key]
    for key in ("total_inflows", "total_outflows", "net_total", "transactions_total"):
        assert now[key] == original[key]
    cleared = storage.apply_convert_edits("an_marks", [{**patch, "review_regions": []}])
    assert cleared["preview_transactions"][0]["review_regions"] == []


@pytest.mark.parametrize("bad", [[{**BOX, "page": 3}], [{**BOX, "page": True}],
    [{**BOX, "width": 2}], [{**BOX, "top": float("nan")}], [BOX] * 51, None])
def test_invalid_manual_regions_fail_atomically(saved, bad):
    storage, path = saved
    before = path.read_bytes()
    with pytest.raises(ValueError):
        storage.apply_convert_edits("an_marks", [{"row_id": "stable", "action": "annotate", "review_regions": bad}])
    assert path.read_bytes() == before


def test_manual_regions_survive_financial_edits_delete_restore_and_legacy_missing_field(saved):
    storage, path = saved
    storage.apply_convert_edits("an_marks", [{"row_id": "stable", "action": "annotate", "review_regions": [BOX]}])
    for action in ("update", "delete", "restore"):
        result = storage.apply_convert_edits("an_marks", [{"row_id": "stable", "action": action,
            "date": "2026-04-02", "description": "EDITED", "credit": 11}])
        assert result["preview_transactions"][0]["review_regions"] == [BOX]
    content = json.loads(path.read_text(encoding="utf-8"))
    for name in ("preview_transactions", "report_transactions"):
        content[name][0].pop("review_regions")
    path.write_text(json.dumps(content), encoding="utf-8")
    result = storage.apply_convert_edits("an_marks", [{"row_id": "stable", "action": "annotate", "review_regions": []}])
    assert result["preview_transactions"][0]["review_regions"] == []


@pytest.mark.parametrize("box", [{**BOX, "page": True}, {**BOX, "left": -0.1},
    {**BOX, "width": 1}, {**BOX, "height": float("inf")}])
def test_request_contract_rejects_invalid_rectangles(box):
    with pytest.raises(ValueError):
        ConvertEditPatch(row_id="stable", action="annotate", review_regions=[box])
