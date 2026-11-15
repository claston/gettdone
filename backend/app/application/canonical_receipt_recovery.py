"""Adopt a retained legacy statement after client identification, without reprocessing."""
from datetime import datetime

from app.application.canonical_movements import MovementScope
from app.application.canonical_pro import CanonicalProService
from app.application.errors import AnalysisEditConflictError, AnalysisNotFoundError
from app.application.parsers.pdf.layout_specific.wise import WISE_CURRENCIES


class CanonicalReceiptRecovery:
    def __init__(self, repository, storage):
        self.repository, self.storage = repository, storage

    def recover(self, owner, job_id):
        repo = self.repository
        # Locate only the caller's source. Revalidate everything after the guard.
        with repo._connect() as conn:
            source = conn.execute(f'''SELECT j.result_analysis_id FROM {repo.p}conversion_jobs j
                JOIN {repo.p}conversion_batches b ON b.batch_id=j.batch_id
                WHERE j.job_id=%s AND b.identity_type='user' AND b.identity_id=%s
                    AND j.identity->>'identity_type'='user' AND j.identity->>'identity_id'=%s''',
                (job_id, owner, owner)).fetchone()
        if not source or not source["result_analysis_id"]:
            return "ineligible"
        analysis_id = source["result_analysis_id"]
        with repo.legacy_guard(analysis_id) as conn:
            selected = conn.execute(f'''SELECT x.organization_id,x.client_id,r.expires_at AS receipt_expires_at
                FROM {repo.p}pro_conversion_contexts x
                JOIN {repo.p}pro_organizations o ON o.id=x.organization_id AND o.owner_user_id=%s
                JOIN {repo.p}pro_received_statements r ON r.organization_id=x.organization_id AND r.job_id=x.job_id
                JOIN {repo.p}conversion_jobs j ON j.job_id=x.job_id
                JOIN {repo.p}conversion_batches b ON b.batch_id=j.batch_id
                WHERE x.job_id=%s AND x.client_id IS NOT NULL AND j.status='completed'
                    AND j.result_analysis_id=%s AND j.expires_at>clock_timestamp() AND b.expires_at>clock_timestamp()
                    AND r.expires_at::timestamptz>clock_timestamp()''', (owner, job_id, analysis_id)).fetchone()
            if not selected:
                return "ineligible"
            scope = MovementScope(owner, selected["organization_id"], job_id, analysis_id, selected["client_id"])
            repo._source(conn, scope)  # Existing lock order: batch/job/organization/user, then context.
            existing = conn.execute(f"SELECT 1 FROM {repo.p}canonical_analyses WHERE analysis_id=%s", (analysis_id,)).fetchone()
            if existing:
                # Includes aliases: never re-import or change flags on an existing authority.
                return "already_canonical"
            cohort = conn.execute(f'''SELECT * FROM {repo.p}canonical_pro_cohorts
                WHERE organization_id=%s FOR SHARE''', (scope.organization_id,)).fetchone()
            if not cohort or not all(cohort[key] for key in ("ingestion_enabled", "read_enabled", "ui_enabled")):
                return "disabled"
            CanonicalProService(repo, None).assert_entitled(conn, owner)
            try:
                candidate = self.storage.read_canonical_candidate(analysis_id)
            except AnalysisNotFoundError:
                return "unavailable"
            except (ValueError, TypeError, KeyError, AnalysisEditConflictError):
                return "review_required"
            if candidate.get("financial_document") is not None:
                return "unsupported"
            # Validate the complete SQL representation before fencing legacy access.
            # Unsupported/ambiguous sources remain usable in their original reader.
            try:
                content = {key: value for key, value in candidate.items() if key not in {"authority", "canonical_completion"}}
                content["expires_at"] = min(datetime.fromisoformat(content["expires_at"]),
                                            datetime.fromisoformat(selected["receipt_expires_at"])).isoformat()
                currency = WISE_CURRENCIES.get(content.get("layout_inference_name"), "BRL")
                with conn.transaction(force_rollback=True):
                    repo.import_legacy(scope, content, currency=currency, connection=conn)
            except (ValueError, TypeError, KeyError, AnalysisEditConflictError):
                return "review_required"
            self.storage.fence_canonical_analysis(analysis_id)
            repo._hook("recovery_fenced")
            repo.import_legacy(scope, content, currency=currency, connection=conn)
            conn.execute(f'''UPDATE {repo.p}canonical_analyses SET read_enabled=%s,edit_enabled=%s,ui_enabled=%s
                WHERE analysis_id=%s''', (cohort["read_enabled"], cohort["edit_enabled"], cohort["ui_enabled"], analysis_id))
            repo._unexpired(conn, scope)
            repo._hook("recovery_before_commit")
        return "recovered"
