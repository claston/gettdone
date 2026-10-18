import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from io import BytesIO
from pathlib import Path
from threading import Event

import pytest

from app.application import s3_analysis_storage as s3_analysis_storage_module
from app.application.errors import AnalysisNotFoundError
from app.application.models import AnalysisData, TransactionRow
from app.application.s3_analysis_storage import S3AnalysisStorage


class FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}

    def put_object(self, **kwargs):
        self.objects[(kwargs["Bucket"], kwargs["Key"])] = bytes(kwargs["Body"])

    def get_object(self, **kwargs):
        return {"Body": BytesIO(self.objects[(kwargs["Bucket"], kwargs["Key"])])}

    def list_objects_v2(self, **kwargs):
        prefix = kwargs["Prefix"]
        return {
            "Contents": [
                {"Key": key}
                for bucket, key in sorted(self.objects)
                if bucket == kwargs["Bucket"] and key.startswith(prefix)
            ],
            "IsTruncated": False,
        }


def _analysis() -> AnalysisData:
    rows = [
        TransactionRow(
            date="2026-08-01",
            description="PIX recebido",
            amount=100.0,
            category="Outros",
            reconciliation_status="unmatched",
        )
    ]
    return AnalysisData(
        analysis_id="an_shared123",
        file_type="pdf",
        upload_filename="extrato.pdf",
        transactions_total=1,
        total_inflows=100.0,
        total_outflows=0.0,
        net_total=100.0,
        preview_transactions=rows,
        report_transactions=rows,
    )


def test_regions_survive_s3_reload_and_a_subsequent_edit(tmp_path):
    from dataclasses import asdict

    from app.application.source_regions import SourceRegion

    client = FakeS3Client()
    storage = S3AnalysisStorage(root_dir=tmp_path / "worker", bucket="private", s3_client=client)
    analysis = _analysis()
    region = SourceRegion(1, 0.1, 0.2, 0.7, 0.03)
    analysis.preview_transactions[0].source_regions = [region]
    storage.save_analysis(analysis)
    restarted = S3AnalysisStorage(root_dir=tmp_path / "api", bucket="private", s3_client=client)
    saved = restarted.apply_convert_edits(analysis.analysis_id, [{"row_id": "row_1", "date": "2026-08-02",
        "description": "EDITED AFTER RELOAD", "credit": 100}], operation_id="region-edit")
    assert saved["preview_transactions"][0]["source_regions"] == [asdict(region)]


def test_s3_edit_receipt_is_committed_last_and_survives_a_new_api_process(tmp_path: Path) -> None:
    client = FakeS3Client()
    storage = S3AnalysisStorage(root_dir=tmp_path / "api1", bucket="private", s3_client=client)
    storage.save_analysis(_analysis())
    edit = [{"action": "insert", "date": "2026-08-02", "description": "Novo", "credit": 25}]
    saved = storage.apply_convert_edits("an_shared123", edit, operation_id="save-1")
    restarted = S3AnalysisStorage(root_dir=tmp_path / "api2", bucket="private", s3_client=client)
    assert restarted.get_convert_edit_operation("an_shared123", "save-1") == saved
    assert restarted.apply_convert_edits("an_shared123", edit, operation_id="save-1") == saved
    assert saved["transactions_total"] == 2


def test_failed_artifact_generation_does_not_commit_edit_or_receipt(tmp_path: Path, monkeypatch) -> None:
    storage = S3AnalysisStorage(root_dir=tmp_path, bucket="private", s3_client=FakeS3Client())
    storage.save_analysis(_analysis())
    original = (tmp_path / "an_shared123" / "analysis.json").read_bytes()

    def fail(*args, **kwargs):
        raise RuntimeError("artifact write failed")

    monkeypatch.setattr(storage.export_artifact_service, "write_convert_artifacts", fail)
    with pytest.raises(RuntimeError, match="artifact write failed"):
        storage.apply_convert_edits("an_shared123", [{"action": "insert", "date": "2026-08-02",
                                   "description": "Novo", "credit": 25}], operation_id="failed-save")
    assert (tmp_path / "an_shared123" / "analysis.json").read_bytes() == original
    assert storage.get_convert_edit_operation("an_shared123", "failed-save") is None


def test_download_cannot_overwrite_working_cache_during_save(tmp_path: Path, monkeypatch) -> None:
    storage = S3AnalysisStorage(root_dir=tmp_path, bucket="private", s3_client=FakeS3Client())
    storage.save_analysis(_analysis())
    storage.set_convert_owner("an_shared123", "user", "owner")
    writing, release, reading = Event(), Event(), Event()
    original = storage.export_artifact_service.write_convert_artifacts

    def pause(*args, **kwargs):
        original(*args, **kwargs)
        writing.set()
        assert release.wait(5)

    def read_owner():
        reading.set()
        storage.assert_convert_owner("an_shared123", "user", "owner")

    monkeypatch.setattr(storage.export_artifact_service, "write_convert_artifacts", pause)
    with ThreadPoolExecutor(max_workers=2) as pool:
        save = pool.submit(storage.apply_convert_edits, "an_shared123", [
            {"action": "insert", "date": "2026-08-02", "description": "Novo", "credit": 25},
        ], operation_id="concurrent-save")
        try:
            assert writing.wait(5)
            read = pool.submit(read_owner)
            assert reading.wait(5)
            with pytest.raises(TimeoutError):
                read.result(timeout=0.05)
        finally:
            release.set()
        assert save.result(timeout=5)["transactions_total"] == 2
        read.result(timeout=5)
    assert storage.get_convert_edit_operation("an_shared123", "concurrent-save")["transactions_total"] == 2


def test_s3_analysis_storage_shares_lambda_results_with_api_process(tmp_path: Path) -> None:
    client = FakeS3Client()
    worker_storage = S3AnalysisStorage(
        root_dir=tmp_path / "lambda",
        bucket="private-conversions",
        prefix="results",
        s3_client=client,
    )
    worker_storage.save_analysis(_analysis())
    worker_storage.set_convert_owner("an_shared123", "user", "usr_123")

    api_storage = S3AnalysisStorage(
        root_dir=tmp_path / "render",
        bucket="private-conversions",
        prefix="results",
        s3_client=client,
    )

    report_path = api_storage.get_convert_report_path("an_shared123", "ofx")
    api_storage.assert_convert_owner("an_shared123", "user", "usr_123")

    assert report_path.exists()
    assert report_path.read_text(encoding="utf-8").startswith("OFXHEADER:100")
    assert any(key.endswith("/analysis.json") for _bucket, key in client.objects)
    assert any(key.endswith("/converted.ofx") for _bucket, key in client.objects)


@pytest.mark.parametrize(
    "relative_key",
    [
        "../escaped.json",
        "nested/report.xlsx",
        "/absolute.json",
        "unexpected.json",
    ],
)
def test_s3_analysis_storage_rejects_unapproved_object_paths(
    tmp_path: Path,
    relative_key: str,
) -> None:
    client = FakeS3Client()
    client.objects[("private-conversions", f"results/an_shared123/{relative_key}")] = b"untrusted"
    storage = S3AnalysisStorage(
        root_dir=tmp_path / "render",
        bucket="private-conversions",
        prefix="results",
        s3_client=client,
    )

    with pytest.raises(ValueError, match="approved analysis artifact"):
        storage.get_convert_report_path("an_shared123", "ofx")

    assert not (tmp_path / "escaped.json").exists()


def test_s3_analysis_storage_rejects_local_cache_path_outside_root(tmp_path: Path, monkeypatch) -> None:
    root_dir = tmp_path / "render"
    storage = S3AnalysisStorage(
        root_dir=root_dir,
        bucket="private-conversions",
        prefix="results",
        s3_client=FakeS3Client(),
    )
    original_realpath = os.path.realpath

    def resolve_outside_root(path) -> str:
        if Path(path).name == "an_shared123":
            return str(tmp_path / "outside")
        return original_realpath(path)

    monkeypatch.setattr(s3_analysis_storage_module.os.path, "realpath", resolve_outside_root)

    with pytest.raises(AnalysisNotFoundError):
        storage._resolve_analysis_dir("an_shared123")
