import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.application import AnalysisNotFoundError, TempAnalysisStorage


def test_storage_rejects_path_traversal_when_resolving_report_path(tmp_path: Path) -> None:
    root_dir = tmp_path / "analyses"
    storage = TempAnalysisStorage(root_dir=root_dir, ttl_seconds=3600)
    external_dir = tmp_path / "external-analysis"
    external_dir.mkdir(parents=True, exist_ok=True)
    (external_dir / "report.xlsx").write_bytes(b"report")
    (external_dir / "analysis.json").write_text(
        json.dumps({"expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}),
        encoding="utf-8",
    )

    with pytest.raises(AnalysisNotFoundError):
        storage.get_report_path("..\\external-analysis")


def test_storage_does_not_cleanup_external_directory_for_traversal_input(tmp_path: Path) -> None:
    root_dir = tmp_path / "analyses"
    storage = TempAnalysisStorage(root_dir=root_dir, ttl_seconds=3600)
    external_dir = tmp_path / "expired-analysis"
    external_dir.mkdir(parents=True, exist_ok=True)
    (external_dir / "report.xlsx").write_bytes(b"report")
    (external_dir / "analysis.json").write_text(
        json.dumps({"expires_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()}),
        encoding="utf-8",
    )

    with pytest.raises(AnalysisNotFoundError):
        storage.get_report_path("..\\expired-analysis")

    assert external_dir.exists()
    assert (external_dir / "analysis.json").exists()
    assert (external_dir / "report.xlsx").exists()
