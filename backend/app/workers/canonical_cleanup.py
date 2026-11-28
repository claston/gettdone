"""Scheduled retained-data cleanup entry point; independent of ingestion flags.

Wire to the deployment's existing maintenance scheduler before enrolling a cohort.
It only removes documents expired under their original analysis/job/batch TTL.
"""
from app.application.canonical_artifacts import CanonicalCleanupService
from app.application.office_documents import OfficeReviews
from app.application.paid_document_cleanup import PaidDocumentCleanup


def handler(event=None, context=None):
    from app.dependencies import (
        get_access_control_service,
        get_async_conversion_report_service,
        get_canonical_pro_service,
        get_conversion_batch_document_store,
        get_report_service,
    )
    access = get_access_control_service()
    with access.db.connect() as conn:
        has_office = access.db.table_exists(conn, 'pro_document_reviews')
    if has_office:
        OfficeReviews(access.db).purge()
    service = get_canonical_pro_service()
    if service is None:
        raise RuntimeError("PostgreSQL is required for canonical cleanup")
    storage = (get_async_conversion_report_service() or get_report_service()).storage
    removed = CanonicalCleanupService(service.repository, service.artifacts.objects, storage).run()
    documents = PaidDocumentCleanup(service.repository, get_conversion_batch_document_store(), storage).run()
    return {"expired_analyses_removed": removed, "expired_pro_documents_removed": documents}


def worker_handler():
    """Use only worker dependencies: its container intentionally excludes FastAPI."""
    import os
    from pathlib import Path

    from app.adapters.conversion.canonical_movements import PostgresMovementRepository
    from app.adapters.conversion.document_retention import analysis_retention_resolver
    from app.adapters.conversion.document_store import S3ConversionDocumentStore
    from app.application.canonical_pro import canonical_objects
    from app.application.s3_analysis_storage import S3AnalysisStorage

    database, schema = os.environ['DATABASE_URL'], os.getenv('DATABASE_SCHEMA', 'public')
    repo = PostgresMovementRepository(database, schema=schema)
    options = dict(bucket=os.environ['CONVERSION_S3_BUCKET'],
                   region=os.getenv('AWS_REGION') or os.getenv('AWS_DEFAULT_REGION'),
                   server_side_encryption=os.getenv('CONVERSION_S3_SERVER_SIDE_ENCRYPTION', 'AES256'),
                   kms_key_id=os.getenv('CONVERSION_S3_KMS_KEY_ID'))
    originals = S3ConversionDocumentStore(prefix=os.getenv('CONVERSION_S3_PREFIX', 'conversion/jobs'), **options)
    storage = S3AnalysisStorage(root_dir=Path('/tmp/gettdone/analyses'),
        prefix=os.getenv('CONVERSION_RESULTS_S3_PREFIX', 'conversion/results'),
        ttl_seconds=int(os.getenv('ANALYSIS_TTL_SECONDS', '86400')),
        retention_resolver=analysis_retention_resolver(database, schema), **options)
    with repo._connect() as conn:
        for table in ('pro_document_link_events', 'pro_document_movement_links',
                      'pro_document_review_events', 'pro_document_reviews', 'office_document_associations'):
            conn.execute(f'DELETE FROM {repo.p}{table} WHERE expires_at::timestamptz<=clock_timestamp()')
    removed = CanonicalCleanupService(repo, canonical_objects(storage), storage).run()
    documents = PaidDocumentCleanup(repo, originals, storage).run()
    return {'expired_analyses_removed': removed, 'expired_pro_documents_removed': documents}


if __name__ == "__main__":
    print(handler())
