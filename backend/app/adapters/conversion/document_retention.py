"""Resolve original/result lifetime from the same retained PRO job authority."""
import re
from datetime import timezone


def analysis_retention_resolver(database_url, schema="public"):
    if not database_url:
        return None
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema):
        raise ValueError("Invalid schema")

    def resolve(analysis_id, job_id=None):
        import psycopg
        with psycopg.connect(database_url) as conn:
            row = conn.execute(f'''SELECT expires_at FROM "{schema}".conversion_jobs
                WHERE product='pro' AND (job_id=%s OR result_analysis_id=%s)
                ORDER BY created_at DESC LIMIT 1''', (job_id, analysis_id)).fetchone()
        return row[0].astimezone(timezone.utc) if row else None

    def register(analysis_id, job_id):
        import psycopg
        from psycopg.types.json import Jsonb
        with psycopg.connect(database_url) as conn:
            row = conn.execute(f'''UPDATE "{schema}".conversion_jobs SET retained_analysis_ids=
                CASE WHEN retained_analysis_ids @> %s THEN retained_analysis_ids ELSE retained_analysis_ids || %s END
                WHERE job_id=%s AND product='pro' AND expires_at>clock_timestamp() AND status<>'expired' RETURNING job_id''',
                (Jsonb([analysis_id]), Jsonb([analysis_id]), job_id)).fetchone()
        if not row:
            from app.application.errors import AnalysisNotFoundError
            raise AnalysisNotFoundError

    resolve.register = register
    return resolve
