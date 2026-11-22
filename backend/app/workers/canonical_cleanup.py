"""Scheduled retained-data cleanup entry point; independent of ingestion flags.

Wire to the deployment's existing maintenance scheduler before enrolling a cohort.
It only removes documents expired under their original analysis/job/batch TTL.
"""
from app.application.canonical_artifacts import CanonicalCleanupService
from app.application.office_documents import OfficeReviews
from app.dependencies import get_access_control_service, get_async_conversion_report_service, get_canonical_pro_service, get_report_service


def handler(event=None, context=None):
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
    return {"expired_analyses_removed": removed}


if __name__ == "__main__":
    print(handler())
