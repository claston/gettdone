"""Immutable export attempts, with publication only for the still-current SQL version."""
import hashlib
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from app.application.errors import AnalysisEditConflictError
from app.application.review_commands import ReviewCommandResult, ReviewCommandService
from app.application.storage_service import TempAnalysisStorage

FILES = frozenset({"report.xlsx", "converted.ofx", "converted.xlsx", "converted.csv"})


class CanonicalArtifactService:
    def __init__(self, repository, objects):
        self.repository, self.objects = repository, objects

    def generate(self, scope):
        with self.repository.artifact_io(scope.analysis_id):
            return self._generate(scope)

    def _generate(self, scope):
        snapshot = self.repository.read(scope)
        version = snapshot["version"]
        current = self.repository.artifact(scope, version=version)
        if current:
            return current
        # Every attempt has its own keys, including simultaneous same-version
        # exports. A failed/losing attempt cannot overwrite published bytes.
        prefix = f"canonical/{scope.analysis_id}/v{version}/{uuid4().hex}"
        manifest = {"version": version, "expires_at": snapshot["expires_at"], "files": {}}
        with TemporaryDirectory(prefix="ofx-canonical-export-") as directory:
            root = Path(directory)
            content = deepcopy(snapshot)
            content.pop("authority", None)
            service = ReviewCommandService()
            prepared = service.prepare_export(content)
            storage = TempAnalysisStorage(root)
            storage._publish_review_artifacts(root, content, ReviewCommandResult(
                report_rows=prepared.report_rows,
                preview_rows=service._parse_transaction_rows(content["preview_transactions"]),
                effective_overrides=prepared.effective_overrides))
            for name in sorted(FILES):
                path = root / name
                if not path.exists():
                    continue  # Some supported statements deliberately cannot export OFX.
                data = path.read_bytes()
                key = f"{prefix}/{name}"
                self.objects.put(key, data)
                manifest["files"][name] = {"key": key, "sha256": hashlib.sha256(data).hexdigest()}
        # Rechecks authorization, TTL and CAS after all external writes. Failure
        # leaves an unreferenced attempt for prefix cleanup, never a stale download.
        return self.repository.publish_artifact(scope, version, manifest)

    def download(self, scope, name):
        if name not in FILES:
            raise ValueError("Unsupported artifact")
        manifest = self.repository.artifact(scope) or self.generate(scope)
        entry = manifest["files"].get(name)
        if entry is None:
            raise ValueError("This statement cannot export the requested format")
        data = self.objects.get(entry["key"])
        if hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise ValueError("Artifact integrity check failed")
        if self.repository.artifact(scope, version=manifest["version"]) != manifest:
            raise AnalysisEditConflictError
        return data


class CanonicalCleanupService:
    """Remove versioned attempts AND the fenced legacy copy within original TTL."""

    def __init__(self, repository, objects, legacy_storage):
        self.repository, self.objects, self.legacy_storage = repository, objects, legacy_storage

    def run(self):
        def delete(analysis_id):
            self.objects.delete_analysis(analysis_id)
            self.legacy_storage.delete_canonical_analysis(analysis_id)
        return self.repository.purge_expired(delete_artifacts=delete)
