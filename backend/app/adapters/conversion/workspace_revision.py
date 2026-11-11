"""Small PostgreSQL validators: never select result payloads or movement rows."""
import hashlib


def workspace_revision(repository, *, owner, batch_id=None, client_id=None):
    p = f'"{repository.database_schema}".'  # Validated by the repository constructor.
    if (batch_id is None) == (client_id is None):
        raise ValueError("Exactly one workspace scope is required")
    with repository._connection() as conn:
        if client_id is not None:
            scope = conn.execute(f'''SELECT c.id, c.xmin::text AS revision FROM {p}pro_clients c
                JOIN {p}pro_organizations o ON o.id=c.organization_id
                WHERE c.id=%s AND o.owner_user_id=%s''', (client_id, owner)).fetchone()
            membership = f'''JOIN {p}pro_received_statements r ON r.job_id=j.job_id AND r.batch_id=j.batch_id
                JOIN {p}pro_conversion_contexts cx ON cx.job_id=j.job_id AND cx.organization_id=r.organization_id
                JOIN {p}pro_organizations ro ON ro.id=r.organization_id AND ro.owner_user_id=b.identity_id'''
            predicate = "cx.client_id=%s AND r.expires_at::timestamptz > clock_timestamp()"
            extra = ", r.xmin::text, cx.xmin::text, ro.xmin::text"
        else:
            scope = conn.execute(f'''SELECT batch_id AS id, xmin::text AS revision FROM {p}conversion_batches
                WHERE batch_id=%s AND identity_type='user' AND identity_id=%s''', (batch_id, owner)).fetchone()
            membership, predicate, extra = "", "b.batch_id=%s", ""
        if scope is None:
            return None
        # xmin changes on every committed row update, including projections whose
        # legacy updated_at is unchanged. Include TTL transitions and all source
        # authority/context rows, also for repeated documents and later pages.
        result = conn.execute(f'''SELECT md5(COALESCE(jsonb_agg(jsonb_build_array(
                j.job_id, j.xmin::text, b.xmin::text, o.xmin::text, u.xmin::text,
                a.xmin::text, sj.xmin::text, sb.xmin::text, x.xmin::text,
                j.expires_at > clock_timestamp(), b.expires_at > clock_timestamp(),
                a.expires_at > clock_timestamp(), sj.expires_at > clock_timestamp(),
                sb.expires_at > clock_timestamp() {extra}) ORDER BY j.job_id)::text, '[]')) AS revision
            FROM {p}conversion_jobs j JOIN {p}conversion_batches b ON b.batch_id=j.batch_id
            LEFT JOIN {p}pro_organizations o ON o.owner_user_id=b.identity_id
            LEFT JOIN {p}users u ON u.id=b.identity_id
            LEFT JOIN {p}canonical_analyses a ON a.analysis_id=j.result_analysis_id
            LEFT JOIN {p}conversion_jobs sj ON sj.job_id=a.job_id
            LEFT JOIN {p}conversion_batches sb ON sb.batch_id=sj.batch_id
            LEFT JOIN {p}pro_conversion_contexts x ON x.organization_id=a.organization_id AND x.job_id=a.job_id
            {membership}
            WHERE b.identity_type='user' AND b.identity_id=%s AND {predicate}''',
            (owner, client_id if client_id is not None else batch_id)).fetchone()
        entitlement = conn.execute(f'''SELECT md5(COALESCE(jsonb_agg(jsonb_build_array(
                s.id, s.xmin::text, p.xmin::text) ORDER BY s.id)::text, '[]')) AS revision
            FROM {p}user_plan_subscriptions s JOIN {p}plan_versions p ON p.id=s.plan_version_id
            WHERE s.user_id=%s''', (owner,)).fetchone()
        # Hash scope as well: a validator from another owner's resource cannot match.
        value = f"v1:{owner}:{batch_id}:{client_id}:{scope['revision']}:{result['revision']}:{entitlement['revision']}"
        return f'W/"{hashlib.sha256(value.encode()).hexdigest()}"'
