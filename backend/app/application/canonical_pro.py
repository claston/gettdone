"""PRO routing and adoption. Disabled controls fail closed, never change authority."""
import logging
from dataclasses import asdict
from time import monotonic

from psycopg.types.json import Jsonb

from app.application.canonical_artifacts import CanonicalArtifactService
from app.application.canonical_identity import receive_in_transaction
from app.application.canonical_movements import CanonicalRollout, MovementScope
from app.application.conversion.contracts.jobs import ConversionJobResultReference
from app.application.conversion.document_conversion_pipeline import DocumentConversionRuntime
from app.application.errors import AnalysisAccessDeniedError, AnalysisEditConflictError
from app.application.parsers.pdf.layout_specific.wise import WISE_CURRENCIES

logger = logging.getLogger(__name__)
PAID_PLANS = {"essencial", "profissional", "escritorio"}


class CanonicalProService:
    def __init__(self, repository, objects):
        self.repository = repository
        self.artifacts = CanonicalArtifactService(repository, objects)

    def assert_entitled(self, conn, actor_id):
        repo = self.repository
        plan = conn.execute(f'''SELECT 1 FROM {repo.p}user_plan_subscriptions s
            JOIN {repo.p}plan_versions p ON p.id = s.plan_version_id
            WHERE s.user_id=%s AND s.status='active' AND p.batch_conversion_enabled
                AND p.code IN ('essencial','profissional','escritorio')''', (actor_id,)).fetchone()
        if not plan:
            raise AnalysisAccessDeniedError

    def resolve(self, actor_id, analysis_id):
        repo = self.repository
        with repo._connect() as conn:
            row = conn.execute(f'''SELECT a.* FROM {repo.p}canonical_analyses a
                JOIN {repo.p}pro_organizations o ON o.id = a.organization_id
                WHERE a.analysis_id = %s AND o.owner_user_id = %s''', (analysis_id, actor_id)).fetchone()
            if row is not None:
                self.assert_entitled(conn, actor_id)
        if row is None:
            return None
        scope = MovementScope(actor_id, row["organization_id"], row["job_id"], analysis_id, row["client_id"])
        repo.read(scope)  # Includes active user, source identity and original TTL.
        return scope

    def read(self, scope):
        repo = self.repository
        with repo._connect() as conn:
            self.assert_entitled(conn, scope.actor_id)
            analysis, _ = repo._analysis(conn, scope)
            if not analysis["read_enabled"]:
                raise AnalysisEditConflictError
            result = repo._snapshot(conn, analysis)
            result["canonical_edit_enabled"] = analysis["edit_enabled"]
            result["canonical_ui_enabled"] = analysis["ui_enabled"]
            repo._unexpired(conn, scope)
            return result

    def edit(self, scope, edits, **options):
        start = monotonic()
        try:
            result = self.repository.apply(scope, edits, channel="pro", require_edit_enabled=True, **options)
        except AnalysisEditConflictError:
            logger.info("canonical_save outcome=conflict analysis_id=%s", scope.analysis_id)
            raise
        except Exception:
            logger.warning("canonical_save outcome=failed analysis_id=%s", scope.analysis_id)
            raise
        logger.info("canonical_save outcome=committed analysis_id=%s version=%s duration_ms=%d",
                    scope.analysis_id, result["version"], (monotonic() - start) * 1000)
        return result

    def download(self, scope, name, *, expected_version=None):
        snapshot = self.read(scope)
        if expected_version is not None and expected_version != snapshot["version"]:
            raise AnalysisEditConflictError
        try:
            data = self.artifacts.download(scope, name)
            if self.read(scope)["version"] != snapshot["version"]:
                raise AnalysisEditConflictError
            return data, snapshot["version"]
        except Exception:
            logger.warning("canonical_export outcome=failed analysis_id=%s version=%s", scope.analysis_id, snapshot["version"])
            raise


class CanonicalCompletion:
    """Called inside completion's SQL transaction, before the job becomes visible.

    Only new paid PRO jobs with an existing document/client context are eligible.
    The worker uses the same storage as extraction. A failed fence/SQL transaction
    leaves no completed result and never routes a fenced document to legacy.
    """
    def __init__(self, repository, storage):
        self.repository, self.storage = repository, storage

    def resume(self, job):
        # Free must not acquire any dependency on canonical storage or SQL.
        if (job.identity.identity_type != "user" or not job.identity.batch_conversion_enabled
                or job.identity.plan_code not in PAID_PLANS):
            return None
        analysis_id = DocumentConversionRuntime.from_job(job).attempt_processing_id
        if analysis_id is None:
            return None
        completion = self.storage.get_canonical_completion(analysis_id)
        if completion is None:
            return None
        if (completion.get("job_id") != job.job_id or completion.get("batch_id") != job.batch_id
                or completion.get("actor_id") != job.identity.identity_id):
            raise AnalysisAccessDeniedError
        result = ConversionJobResultReference(**completion["result"])
        if result.analysis_id != analysis_id:
            raise AnalysisAccessDeniedError
        return result

    def __call__(self, conn, record):
        identity = record.job.identity
        if (identity.identity_type != "user" or not identity.batch_conversion_enabled
                or identity.plan_code not in PAID_PLANS):
            return
        repo = self.repository
        # Identify within completion, before selecting authority. The cohort must
        # already exist; this does not enable ingestion or migrate exposed jobs.
        selected = conn.execute(f'''SELECT c.* FROM {repo.p}canonical_pro_cohorts c
            JOIN {repo.p}pro_organizations o ON o.id=c.organization_id
            WHERE o.owner_user_id=%s FOR UPDATE OF o''', (identity.identity_id,)).fetchone()
        if selected and selected["ingestion_enabled"] and selected["ui_enabled"]:
            receive_in_transaction(repo, conn, record)
        cohort = conn.execute(f'''SELECT c.*, x.client_id FROM {repo.p}canonical_pro_cohorts c
            JOIN {repo.p}pro_organizations o ON o.id = c.organization_id
            JOIN {repo.p}pro_conversion_contexts x ON x.organization_id = o.id AND x.job_id = %s
            WHERE o.owner_user_id = %s''', (record.job.job_id, identity.identity_id)).fetchone()
        if not cohort or not (cohort["ingestion_enabled"] or cohort["shadow_enabled"]):
            if self.storage.get_canonical_completion(record.result.analysis_id) is not None:
                raise ConnectionError("Canonical adoption is paused for a pending completion")
            return
        CanonicalProService(repo, None).assert_entitled(conn, identity.identity_id)
        analysis = (record.result.payload or {}).get("analysis") or {}
        if analysis.get("financial_document") is not None:
            return
        scope = MovementScope(identity.identity_id, cohort["organization_id"], record.job.job_id,
                              record.result.analysis_id, cohort["client_id"])
        if not cohort["ingestion_enabled"]:
            if self.storage.get_canonical_completion(scope.analysis_id) is not None:
                raise ConnectionError("Canonical adoption is paused for a pending completion")
            # Read-only shadow: compare the job projection against extraction storage.
            if hasattr(self.storage, "_materialize_analysis"):
                self.storage._materialize_analysis(scope.analysis_id)
            content, _ = self.storage._read_analysis_metadata(analysis_id=scope.analysis_id, metadata_filename="analysis.json")
            differences = [key for key in ("preview_transactions", "opening_balance", "closing_balance", "updated_at")
                           if key in analysis and analysis[key] != content.get(key)]
            # Exercise the real SQL representation in a rolled-back savepoint.
            # No marker, movement, projection or revision survives shadow mode.
            try:
                with conn.transaction(force_rollback=True):
                    projected = repo.ingest(scope, content,
                        currency=WISE_CURRENCIES.get(content.get("layout_inference_name"), "BRL"),
                        product="pro", new_analysis=True,
                        rollout=CanonicalRollout(True, frozenset({scope.organization_id})), connection=conn)
                    differences.extend(key for key in ("preview_transactions", "report_transactions", "opening_balance",
                                       "closing_balance", "total_inflows", "total_outflows", "net_total")
                                       if projected.get(key) != content.get(key))
            except Exception as exc:
                differences.append(type(exc).__name__)
            logger.info("canonical_shadow analysis_id=%s differences=%s", scope.analysis_id, differences)
            return
        content = self.storage.fence_canonical_analysis(scope.analysis_id, completion={
            "job_id": record.job.job_id, "batch_id": record.job.batch_id,
            "actor_id": identity.identity_id, "result": asdict(record.result),
        })
        # Exact bytes + owner + confirmed client identify a repeated document,
        # never a transaction inferred from date/amount/description. The original
        # authority and expiration remain unchanged, including human corrections.
        # The organization lock above serializes concurrent reimports.
        repeated = conn.execute(f'''SELECT a.*, LEAST(a.expires_at,j.expires_at,b.expires_at) AS source_expires_at
            FROM {repo.p}canonical_analyses a
            JOIN {repo.p}conversion_jobs j ON j.job_id=a.job_id
            JOIN {repo.p}conversion_batches b ON b.batch_id=j.batch_id
            WHERE a.organization_id=%s AND a.client_id=%s AND a.analysis_id<>%s
                AND j.document->>'sha256_hex'=%s AND j.document->>'file_type'=%s
                AND j.status='completed' AND b.identity_id=%s
                AND LEAST(a.expires_at,j.expires_at,b.expires_at)>clock_timestamp()
            ORDER BY a.created_at,a.analysis_id LIMIT 1 FOR UPDATE OF a''',
            (scope.organization_id, scope.client_id, scope.analysis_id, record.job.document.sha256_hex,
             record.job.document.file_type, identity.identity_id)).fetchone() if cohort["ui_enabled"] else None
        if repeated:
            snapshot = repo._snapshot(conn, repeated)
            payload = {**(record.result.payload or {}), "analysis": snapshot, "authority": "postgres_v1",
                       "canonical_source_job_id": repeated["job_id"]}
            if snapshot.get("reviewed_at"):
                payload["reviewed_at"] = snapshot["reviewed_at"]
            conn.execute(f'''UPDATE {repo.p}conversion_jobs SET result_analysis_id=%s,result_payload=%s,
                expires_at=LEAST(expires_at,%s) WHERE job_id=%s''',
                (repeated["analysis_id"], Jsonb(payload), repeated["source_expires_at"], scope.job_id))
            conn.execute(f'''UPDATE {repo.p}pro_received_statements SET expires_at=%s
                WHERE organization_id=%s AND job_id=%s AND expires_at>%s''',
                (repeated["source_expires_at"].isoformat(), scope.organization_id, scope.job_id,
                 repeated["source_expires_at"].isoformat()))
            return
        repo.ingest(scope, content, currency=WISE_CURRENCIES.get(content.get("layout_inference_name"), "BRL"),
                    product="pro", new_analysis=True,
                    rollout=CanonicalRollout(True, frozenset({scope.organization_id})), connection=conn,
                    resolve_accounts=bool(cohort["ui_enabled"]))
        conn.execute(f'''UPDATE {repo.p}canonical_analyses SET read_enabled=%s, edit_enabled=%s, ui_enabled=%s
            WHERE analysis_id=%s''',
                     (cohort["read_enabled"], cohort["edit_enabled"], cohort["ui_enabled"], scope.analysis_id))


def canonical_objects(storage):
    from app.adapters.conversion.canonical_artifacts import LocalCanonicalObjects, S3CanonicalObjects
    from app.application.s3_analysis_storage import S3AnalysisStorage

    if isinstance(storage, S3AnalysisStorage):
        encryption = {"ServerSideEncryption": storage.server_side_encryption or "AES256"}
        if storage.kms_key_id:
            encryption["SSEKMSKeyId"] = storage.kms_key_id
        return S3CanonicalObjects(storage._client(), storage.bucket, prefix=storage.prefix, encryption_options=encryption)
    return LocalCanonicalObjects(storage.root_dir)
