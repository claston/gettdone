from __future__ import annotations

import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from app.application.analysis_lock import locked_analysis
from app.application.errors import AnalysisNotFoundError
from app.application.models import AnalysisData
from app.application.storage_service import TempAnalysisStorage


def _approved_analysis_artifact_path(analysis_dir: Path, relative: str) -> Path:
    """Map an untrusted S3 key suffix to a path built only from literals."""
    if relative == "analysis.json":
        return analysis_dir / "analysis.json"
    if relative == "report.xlsx":
        return analysis_dir / "report.xlsx"
    if relative == "converted.csv":
        return analysis_dir / "converted.csv"
    if relative == "converted.ofx":
        return analysis_dir / "converted.ofx"
    if relative == "converted.xlsx":
        return analysis_dir / "converted.xlsx"
    if relative == "reconcile.json":
        return analysis_dir / "reconcile.json"
    if relative == "reconcile_report.csv":
        return analysis_dir / "reconcile_report.csv"
    if relative == "reconcile_report.xlsx":
        return analysis_dir / "reconcile_report.xlsx"
    raise ValueError("S3 object is not an approved analysis artifact.")


class S3AnalysisStorage(TempAnalysisStorage):
    """S3-backed analysis artifacts with a local working cache.

    Conversion code can keep using filesystem paths while Lambda and the API
    share the canonical result objects through a private bucket.
    """

    def __init__(
        self,
        *,
        root_dir: Path,
        bucket: str,
        prefix: str = "conversion/results",
        region: str | None = None,
        server_side_encryption: str = "AES256",
        kms_key_id: str | None = None,
        s3_client: Any | None = None,
        **kwargs,
    ) -> None:
        super().__init__(root_dir=root_dir, **kwargs)
        self.bucket = (bucket or "").strip()
        if not self.bucket:
            raise ValueError("S3 analysis storage bucket is required.")
        self.prefix = (prefix or "").strip().strip("/")
        self.region = (region or "").strip() or None
        self.server_side_encryption = (server_side_encryption or "").strip()
        self.kms_key_id = (kms_key_id or "").strip() or None
        if self.kms_key_id and self.server_side_encryption != "aws:kms":
            raise ValueError("An S3 KMS key requires server-side encryption set to aws:kms.")
        self._s3_client = s3_client

    def _resolve_analysis_dir(self, analysis_id: str) -> Path:
        candidate = super()._resolve_analysis_dir(analysis_id)
        safe_root = os.path.realpath(self.root_dir)
        normalized_candidate = os.path.realpath(candidate)
        safe_prefix = f"{safe_root.rstrip(os.sep)}{os.sep}"
        if not normalized_candidate.startswith(safe_prefix):
            raise AnalysisNotFoundError
        return Path(normalized_candidate)

    def save_analysis(self, data: AnalysisData) -> str:
        expires_at = super().save_analysis(data)
        self._upload_analysis(data.analysis_id)
        return expires_at

    @locked_analysis
    def get_report_path(self, analysis_id: str) -> Path:
        self._materialize_analysis(analysis_id)
        return super().get_report_path(analysis_id)

    @locked_analysis
    def get_convert_report_path(self, analysis_id: str, file_format: str, **kwargs) -> Path:
        self._materialize_analysis(analysis_id)
        path = super().get_convert_report_path(analysis_id, file_format, **kwargs)
        if kwargs:
            self._upload_analysis(analysis_id)
        return path

    @locked_analysis
    def get_upload_filename(self, analysis_id: str) -> str | None:
        self._materialize_analysis(analysis_id)
        return super().get_upload_filename(analysis_id)

    @locked_analysis
    def set_report_owner(self, analysis_id: str, identity_type: str, identity_id: str) -> None:
        self._materialize_analysis(analysis_id)
        super().set_report_owner(analysis_id, identity_type, identity_id)
        self._upload_analysis(analysis_id)

    @locked_analysis
    def assert_report_owner(
        self,
        analysis_id: str,
        identity_type: str,
        identity_id: str,
        *,
        allow_unowned: bool = False,
    ) -> None:
        self._materialize_analysis(analysis_id)
        super().assert_report_owner(
            analysis_id,
            identity_type,
            identity_id,
            allow_unowned=allow_unowned,
        )

    @locked_analysis
    def set_convert_owner(self, analysis_id: str, identity_type: str, identity_id: str) -> None:
        self._materialize_analysis(analysis_id)
        super().set_convert_owner(analysis_id, identity_type, identity_id)
        self._upload_analysis(analysis_id)

    @locked_analysis
    def assert_convert_owner(self, analysis_id: str, identity_type: str, identity_id: str) -> None:
        self._materialize_analysis(analysis_id)
        super().assert_convert_owner(analysis_id, identity_type, identity_id)

    @locked_analysis
    def get_convert_edit_operation(self, analysis_id: str, operation_id: str) -> dict[str, object] | None:
        self._materialize_analysis(analysis_id)
        return super().get_convert_edit_operation(analysis_id, operation_id)

    @locked_analysis
    def apply_convert_edits(self, analysis_id: str, edits: list[dict[str, object]], **kwargs) -> dict[str, object]:
        self._materialize_analysis(analysis_id)
        result = super().apply_convert_edits(analysis_id, edits, **kwargs)
        self._upload_analysis(analysis_id)
        return result

    def save_reconcile_report(
        self,
        summary: dict[str, int],
        reconciliation_rows: list[dict[str, str | float | None]],
        problems: list[dict[str, str]],
    ) -> tuple[str, str]:
        analysis_id, expires_at = super().save_reconcile_report(summary, reconciliation_rows, problems)
        self._upload_analysis(analysis_id)
        return analysis_id, expires_at

    @locked_analysis
    def get_reconcile_report_path(self, analysis_id: str, file_format: str) -> Path:
        self._materialize_analysis(analysis_id)
        return super().get_reconcile_report_path(analysis_id, file_format)

    @locked_analysis
    def set_reconcile_owner(self, analysis_id: str, identity_type: str, identity_id: str) -> None:
        self._materialize_analysis(analysis_id)
        super().set_reconcile_owner(analysis_id, identity_type, identity_id)
        self._upload_analysis(analysis_id)

    @locked_analysis
    def assert_reconcile_owner(
        self,
        analysis_id: str,
        identity_type: str,
        identity_id: str,
        *,
        allow_unowned: bool = False,
    ) -> None:
        self._materialize_analysis(analysis_id)
        super().assert_reconcile_owner(
            analysis_id,
            identity_type,
            identity_id,
            allow_unowned=allow_unowned,
        )

    def _upload_analysis(self, analysis_id: str) -> None:
        analysis_dir = self._resolve_analysis_dir(analysis_id)
        if not analysis_dir.exists():
            return
        for path in sorted(analysis_dir.rglob("*"), key=lambda item: item.name == "analysis.json"):
            if not path.is_file():
                continue
            relative = path.relative_to(analysis_dir).as_posix()
            params: dict[str, Any] = {
                "Bucket": self.bucket,
                "Key": self._object_key(analysis_id, relative),
                "Body": path.read_bytes(),
                "ContentType": self._content_type(path.suffix.lower()),
            }
            if self.server_side_encryption:
                params["ServerSideEncryption"] = self.server_side_encryption
            if self.kms_key_id:
                params["SSEKMSKeyId"] = self.kms_key_id
            self._client().put_object(**params)

    def _materialize_analysis(self, analysis_id: str) -> None:
        analysis_dir = self._resolve_analysis_dir(analysis_id)
        remote_prefix = self._object_key(analysis_id, "")
        continuation_token: str | None = None
        while True:
            params: dict[str, Any] = {"Bucket": self.bucket, "Prefix": remote_prefix}
            if continuation_token:
                params["ContinuationToken"] = continuation_token
            response = self._client().list_objects_v2(**params)
            for item in response.get("Contents") or []:
                key = str(item.get("Key") or "")
                relative = key[len(remote_prefix) :]
                if not relative:
                    continue
                target = _approved_analysis_artifact_path(analysis_dir, relative)
                if target.parent != analysis_dir:
                    raise ValueError("S3 analysis object escapes the local analysis directory.")
                body = self._client().get_object(Bucket=self.bucket, Key=key)["Body"]
                try:
                    raw_bytes = body.read()
                finally:
                    close = getattr(body, "close", None)
                    if callable(close):
                        close()
                analysis_dir.mkdir(parents=True, exist_ok=True)
                with NamedTemporaryFile(dir=analysis_dir, suffix=".tmp", delete=False) as temporary:
                    temporary_path = Path(temporary.name)
                    temporary.write(raw_bytes)
                try:
                    os.replace(temporary_path, target)
                finally:
                    temporary_path.unlink(missing_ok=True)
            if not response.get("IsTruncated"):
                break
            continuation_token = str(response.get("NextContinuationToken") or "").strip() or None
            if continuation_token is None:
                break

    def _object_key(self, analysis_id: str, relative: str) -> str:
        base = f"{analysis_id}/"
        if self.prefix:
            base = f"{self.prefix}/{base}"
        return f"{base}{relative}"

    def _client(self):
        if self._s3_client is None:
            try:
                import boto3
            except Exception as exc:  # pragma: no cover - installed in production
                raise RuntimeError("S3 analysis storage requires boto3.") from exc
            self._s3_client = boto3.session.Session(region_name=self.region).client("s3")
        return self._s3_client

    @staticmethod
    def _content_type(suffix: str) -> str:
        return {
            ".csv": "text/csv",
            ".json": "application/json",
            ".ofx": "application/x-ofx",
            ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        }.get(suffix, "application/octet-stream")
