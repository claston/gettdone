"""One import review, reusing the converter and existing organization authority."""
from fastapi import APIRouter, Depends, HTTPException

from app.application.document_conference import DocumentConferenceService
from app.application.errors import AnalysisEditConflictError, AnalysisNotFoundError
from app.application.office_documents import OfficeReviews
from app.application.organization_suggestions import build_source_evidence, suggest_organization
from app.dependencies import (
    get_async_conversion_report_service,
    get_canonical_pro_service,
    get_conversion_batch_service,
    get_report_service,
)
from app.routers.office_documents import current_record, describe, owned_record
from app.routers.pro_organization import organization_errors, organization_owner, organization_service
from app.routers.pro_security import require_pro_browser_request
from app.security_baseline import read_bool_env

router = APIRouter(prefix='/api/pro/documents', tags=['Import review'])


def preparation(job_id, owner, service, batches, reports, *, automatic):
    record = current_record(owned_record(batches, owner, job_id), owner, reports)
    if record.status != 'completed' or not record.result:
        raise HTTPException(409, 'Aguarde a leitura do documento antes de revisar.')
    catalog = service.catalog(owner)
    context = service.contexts(owner, [job_id]).get(job_id, {
        'client_id': None, 'account_id': None, 'period_ids': [], 'version': 0})
    payload = record.result.payload or {}
    source = payload.get('organization_source')
    if not isinstance(source, dict):
        source = build_source_evidence({key: value for key, value in (payload.get('analysis') or {}).items()
                                       if key in {'financial_document', 'preview_transactions'}})
    suggestions = suggest_organization(source, catalog)
    client_matches = suggestions['clients']
    organization_locked = payload.get('authority') == 'postgres_v1'
    identity_notice = None
    if source.get('confidence') == 'ambiguous' or any(c['confidence'] == 'ambiguous' for c in client_matches):
        identity_notice = 'Há mais de uma possibilidade de titular. Confira o cliente no documento original.'
    elif context['client_id'] and any(c['confidence'] == 'strong' and c['id'] != context['client_id'] for c in client_matches):
        identity_notice = 'O titular identificado difere do cliente selecionado. Confira a associação antes de confirmar.'
    associated = False
    if (automatic and not organization_locked and not context['client_id'] and source.get('confidence') != 'ambiguous'
            and len(client_matches) == 1 and client_matches[0]['confidence'] == 'strong'):
        accounts = [a for a in suggestions['accounts'] if a['client_id'] == client_matches[0]['id']
                    and a['confidence'] == 'strong']
        with organization_errors():
            context = service.save_context(owner, job_id=job_id, client_id=client_matches[0]['id'],
                account_id=accounts[0]['id'] if len(accounts) == 1 else None,
                period_ids=context['period_ids'], expected_version=context['version'])
        associated = True
    info = describe(record, context, OfficeReviews(service.db), owner)
    info.pop('analysis')
    matching = None
    if (automatic and context['client_id'] and not info['is_bank']
            and info['document_type'] in {'bank_receipt', 'fiscal_invoice', 'support_document'}
            and read_bool_env('PRO_FINANCIAL_CENTER_ENABLED', default=False)):
        canonical = get_canonical_pro_service()
        if canonical is not None:
            conference = DocumentConferenceService(canonical.repository,
                current_document=lambda actor, job: current_record(owned_record(batches, actor, job), actor, reports))
            try:
                matching = conference.suggestions(owner, context['client_id'], job_id, automatic=True)
            except (AnalysisEditConflictError, AnalysisNotFoundError):
                matching = {'matches': [], 'partial': True, 'notice': 'Os documentos mudaram. Atualize para procurar vínculos novamente.'}
    return {'job_id': job_id, 'batch_id': record.job.batch_id, 'filename': record.job.document.filename,
            'context': context, 'catalog': catalog, 'suggestions': suggestions, 'automatically_associated': associated,
            'organization_locked': organization_locked, 'identity_notice': identity_notice,
            'matching': matching, **info}


@router.get('/{job_id}/preparation')
def read_preparation(job_id: str, owner=Depends(organization_owner), service=Depends(organization_service),
                     batches=Depends(get_conversion_batch_service), report=Depends(get_report_service),
                     async_report=Depends(get_async_conversion_report_service)):
    return preparation(job_id, owner, service, batches, (async_report, report), automatic=False)


@router.post('/{job_id}/preparation', dependencies=[Depends(require_pro_browser_request)])
def prepare(job_id: str, owner=Depends(organization_owner), service=Depends(organization_service),
            batches=Depends(get_conversion_batch_service), report=Depends(get_report_service),
            async_report=Depends(get_async_conversion_report_service)):
    return preparation(job_id, owner, service, batches, (async_report, report), automatic=True)
