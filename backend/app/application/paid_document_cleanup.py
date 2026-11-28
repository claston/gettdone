"""Retryable physical deletion, after canonical cleanup, under expired job locks."""
from app.application.conversion.contracts.documents import ConversionDocumentReference


class PaidDocumentCleanup:
    def __init__(self, repository, originals, results):
        self.repo, self.originals, self.results = repository, originals, results

    def run(self):
        removed = 0
        with self.repo._connect() as conn:
            rows = conn.execute(f"""SELECT j.* FROM {self.repo.p}conversion_jobs j
                WHERE j.product='pro' AND j.expires_at<=clock_timestamp() AND j.pro_document_purged_at IS NULL
                AND NOT EXISTS (SELECT 1 FROM {self.repo.p}canonical_analyses a WHERE a.job_id=j.job_id)
                ORDER BY j.expires_at LIMIT 100 FOR UPDATE OF j SKIP LOCKED""").fetchall()
            for row in rows:
                self.originals.delete(ConversionDocumentReference(**row['document']))
                identifiers = set(row['retained_analysis_ids']) | ({row['result_analysis_id']} if row['result_analysis_id'] else set())
                for analysis_id in identifiers:
                    self.results.delete_expired_pro_analysis(analysis_id)
                # Expired jobs are tombstones: renewal cannot revive deleted data.
                conn.execute(f"""UPDATE {self.repo.p}conversion_jobs SET status='expired',result_payload=NULL,result_analysis_id=NULL,
                    pro_document_purged_at=clock_timestamp() WHERE job_id=%s""", (row['job_id'],))
                removed += 1
        return removed
