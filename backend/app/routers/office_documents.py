"""Authorized, transient office delivery over the existing document authority."""
import csv
import json
import re
from dataclasses import replace
from datetime import datetime, timezone
from io import BytesIO, StringIO
from typing import Annotated, Literal
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from app.application.errors import AnalysisAccessDeniedError, AnalysisEditConflictError, AnalysisNotFoundError
from app.application.office_documents import OfficeReviews, document_period, fingerprint, matches_month, record_source
from app.dependencies import (
    get_access_control_service,
    get_async_conversion_report_service,
    get_conversion_batch_document_store,
    get_conversion_batch_service,
    get_report_service,
)
from app.routers.canonical_pro import canonical_display_record, export_canonical_record, validate_export_checks
from app.routers.pro_organization import organization_errors, organization_owner, organization_service
from app.routers.pro_security import require_pro_browser_request

router = APIRouter(prefix='/api/pro/documents', tags=['Office delivery'])
MAX_PACKAGE_BYTES = 64 * 1024 * 1024


@router.get('/options')
def options(owner=Depends(organization_owner)):
    # Preserve the response for already loaded clients; access is the existing PRO entitlement.
    return {'enabled': True, 'max_package_files': 30, 'max_package_bytes': MAX_PACKAGE_BYTES}


def owned_record(batches, owner, job_id, *, allow_expired=False):
    if batches is None:
        raise HTTPException(503, 'Não foi possível consultar o documento. Tente novamente.')
    record = batches.repository.get(job_id)
    if not record or record.job.identity.identity_type != 'user' or record.job.identity.identity_id != owner:
        raise HTTPException(404, 'Documento não encontrado.')
    if not allow_expired and (record.expires_at <= datetime.now(timezone.utc)
                              or str(getattr(record.status, 'value', record.status)) == 'expired'):
        raise HTTPException(410, 'O prazo de acesso a este documento terminou.')
    return record


def current_record(record, owner, reports=()):
    try:
        record = canonical_display_record(record, owner)
    except HTTPException as exc:
        if exc.status_code == 404:
            raise HTTPException(404, 'A revisão deste documento não está disponível. Atualize a lista de documentos.') from None
        raise
    if not record.result:
        return record
    if (record.result.payload or {}).get('authority') == 'postgres_v1':
        expires = (record.result.payload.get('analysis') or {}).get('expires_at')
        if expires:
            try:
                record = replace(record, expires_at=min(record.expires_at, datetime.fromisoformat(str(expires))))
            except (TypeError, ValueError):
                raise HTTPException(409, 'Não foi possível confirmar a disponibilidade deste documento. Atualize a lista.') from None
        return record
    # Legacy artifacts may have committed even when the batch projection failed.
    # Read the same authorized authority used by the editor and downloads.
    for report in reports:
        if report is None:
            continue
        try:
            report.assert_convert_owner(record.result.analysis_id, 'user', owner)
            data = report.storage.read_canonical_candidate(record.result.analysis_id)
            if data.get('authority'):
                raise HTTPException(409, 'A revisão mudou. Atualize os documentos antes de exportar.')
            payload = {**record.result.payload, 'analysis': data}
            reviewed = payload.get('reviewed_at')
            if reviewed and str(data.get('updated_at') or '') > reviewed:
                payload['reviewed_at'] = None
            return replace(record, result=replace(record.result, payload=payload),
                           expires_at=min(record.expires_at, datetime.fromisoformat(data['expires_at'])))
        except AnalysisNotFoundError:
            continue
        except (AnalysisAccessDeniedError, AnalysisEditConflictError):
            raise HTTPException(409, 'Não foi possível confirmar a versão deste documento. Atualize a lista.') from None
    return record


def describe(record, context, reviews, owner):
    payload = record.result.payload if record.result else {}
    analysis = (payload or {}).get('analysis') or {}
    source = record_source(record, context)
    verification = reviews.read(owner, record.job.job_id, source)
    start, end = document_period(analysis)
    bank = analysis.get('financial_document') is None and analysis.get('document_type') in {
        'bank_statement', 'credit_card_statement', 'prepaid_card_statement', 'ofx_statement', 'financial_spreadsheet'}
    return {'source': source, 'verification': verification, 'bank_reviewed': bool(bank and payload.get('reviewed_at')),
            'manual_association': reviews.manual_association(owner, record.job.job_id, context.get('version', 0)),
            'is_bank': bank, 'document_type': verification['document_type'] or analysis.get('document_type') or 'unknown',
            'movement_start_date': start, 'movement_end_date': end,
            'parts': analysis.get('document_parts') or [], 'analysis': analysis}


class Verification(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source: str = Field(pattern=r'^[a-f0-9]{64}$')
    expected_version: int = Field(ge=0)
    operation_id: str = Field(min_length=1, max_length=100)
    status: Literal['pending', 'verified', 'attention', 'unprocessable']
    document_type: Literal['bank_statement', 'bank_receipt', 'fiscal_invoice', 'support_document', 'unknown'] | None = None


@router.post('/{job_id}/verification', dependencies=[Depends(require_pro_browser_request)])
def verify(job_id: str, request: Verification, owner=Depends(organization_owner), service=Depends(organization_service),
           batches=Depends(get_conversion_batch_service), report=Depends(get_report_service),
           async_report=Depends(get_async_conversion_report_service), access=Depends(get_access_control_service)):
    record = current_record(owned_record(batches, owner, job_id), owner, (async_report, report))
    context = service.contexts(owner, [job_id]).get(job_id, {})
    source = record_source(record, context)
    if source != request.source:
        raise HTTPException(409, 'O documento mudou. Atualize a lista antes de confirmar.')
    analysis = (record.result.payload or {}).get('analysis', {}) if record.result else {}
    bank_type = analysis.get('financial_document') is None and analysis.get('document_type') in {
        'bank_statement', 'credit_card_statement', 'prepaid_card_statement', 'ofx_statement', 'financial_spreadsheet'}
    if request.document_type and (bank_type or any(not row.get('is_deleted') for row in analysis.get('preview_transactions', []))):
        raise HTTPException(409, 'Altere o tipo no revisor para conferir os movimentos antes de confirmar.')
    with organization_errors():
        result = OfficeReviews(service.db).save(owner, job_id, source, record.expires_at,
            request.status, request.document_type, request.expected_version, request.operation_id)
    from app.application.office_telemetry import office_event
    if request.status == 'verified':
        office_event(access, owner, 'office_document_verified')
    return result


def original_bytes(record, store):
    if record.job.document.size_bytes > 10 * 1024 * 1024:
        raise HTTPException(413, 'O documento excede o limite de 10 MB.')
    data = store.load(record.job.document).raw_bytes
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(413, 'O documento excede o limite de 10 MB.')
    return data


def part_bytes(raw, part):
    from pypdf import PdfReader, PdfWriter
    reader, writer = PdfReader(BytesIO(raw)), PdfWriter()
    pages = part.get('pages') or []
    if part.get('requires_review') or not pages or any(not isinstance(p, int) or p < 1 or p > len(reader.pages) for p in pages):
        raise HTTPException(409, 'Confira estas páginas no PDF original; a separação não foi confirmada.')
    for page in pages:
        writer.add_page(reader.pages[page - 1])
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


@router.get('/{job_id}/parts/{part_id}')
def download_part(job_id: str, part_id: str, owner=Depends(organization_owner), batches=Depends(get_conversion_batch_service),
                  store=Depends(get_conversion_batch_document_store)):
    record = current_record(owned_record(batches, owner, job_id), owner)
    analysis = (record.result.payload or {}).get('analysis') or {} if record.result else {}
    part = next((p for p in analysis.get('document_parts', []) if p.get('id') == part_id), None)
    if record.job.document.file_type != 'pdf' or part is None:
        raise HTTPException(404, 'Páginas não encontradas.')
    data = part_bytes(original_bytes(record, store), part)
    refreshed = current_record(owned_record(batches, owner, job_id), owner)
    if refreshed.expires_at <= datetime.now(timezone.utc):
        raise HTTPException(410, 'O prazo de acesso a este documento terminou.')
    return Response(data, media_type='application/pdf', headers={
        'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff',
        'Content-Disposition': 'inline; filename="paginas.pdf"'})


class PackageRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    client_id: str = Field(min_length=1, max_length=100)
    job_ids: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(min_length=1, max_length=30)
    month: str = Field(default='', pattern=r'^(|unknown|\d{4}-(0[1-9]|1[0-2]))$')
    snapshot: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')
    accept_partial: bool = False


REASONS = {
    'expired': 'Prazo de acesso encerrado', 'duplicate': 'Origem repetida na seleção',
    'client': 'Associado a outro cliente', 'month': 'Fora do mês selecionado ou período a confirmar',
    'processing': 'Processamento ainda não concluído', 'review': 'Revisão bancária pendente',
    'verification': 'Documento a conferir', 'attention': 'Documento precisa de atenção',
    'missing': 'Arquivo indisponível para download',
}


def package_snapshot(request, owner, service, batches, reports):
    with organization_errors(), service.db.connect() as conn:
        service._entity(conn, 'pro_clients', service._organization(conn, owner), request.client_id)
    contexts = service.contexts(owner, request.job_ids)
    reviews = OfficeReviews(service.db)
    reviews.prefetch(owner, request.job_ids)
    items, records, sources, seen = [], {}, {}, set()
    for job_id in sorted(set(request.job_ids)):
        record = owned_record(batches, owner, job_id, allow_expired=True)
        if record.expires_at <= datetime.now(timezone.utc) or str(getattr(record.status, 'value', record.status)) == 'expired':
            items.append({'job_id': job_id, 'included': False, 'reason': REASONS['expired']})
            continue  # Never leak expired names or account metadata into the manifest.
        record = current_record(record, owner, reports)
        if record.expires_at <= datetime.now(timezone.utc) or str(getattr(record.status, 'value', record.status)) == 'expired':
            items.append({'job_id': job_id, 'included': False, 'reason': REASONS['expired']})
            continue
        context = contexts.get(job_id, {})
        info = describe(record, context, reviews, owner)
        analysis = info['analysis']
        reason = None
        duplicate = record.job.document.sha256_hex
        if context.get('client_id') != request.client_id:
            reason = 'client'
        elif not matches_month(analysis, request.month):
            reason = 'month'
        elif duplicate in seen:
            reason = 'duplicate'
        elif info['verification']['status'] in {'attention', 'unprocessable'}:
            reason = 'attention'
        elif info['is_bank'] and not info['bank_reviewed']:
            reason = 'review'
        elif not info['is_bank'] and info['verification']['status'] != 'verified':
            reason = 'verification'
        elif info['is_bank'] and str(getattr(record.status, 'value', record.status)) != 'completed':
            reason = 'processing'
        if reason is None:
            seen.add(duplicate)
        account = str(analysis.get('account_number') or '')
        cautions = []
        if not info['movement_start_date']:
            cautions.append('Período a confirmar no documento original.')
        if analysis.get('warnings'):
            cautions.append('Há avisos de extração. Confira o documento no revisor antes da entrega.')
        if (analysis.get('statement_balance_check') or {}).get('difference'):
            cautions.append('O saldo informado não confere com os movimentos. Confira o extrato no revisor.')
        if any(part.get('requires_review') for part in info['parts']):
            cautions.append('Há páginas cuja separação precisa de conferência no original.')
        items.append({'job_id': job_id, 'batch_id': record.job.batch_id, 'filename': record.job.document.filename,
            'included': reason is None, 'reason': REASONS.get(reason), 'document_type': info['document_type'],
            'bank_reviewed': info['bank_reviewed'], 'verification': info['verification'],
            'bank_code': analysis.get('bank_code'), 'account': ('••••' + account[-4:]) if account else None,
            'start_date': info['movement_start_date'], 'end_date': info['movement_end_date'],
            'expires_at': record.expires_at.isoformat(), 'source_sha256': duplicate,
            'version': analysis.get('version') or analysis.get('updated_at'),
            'parts': info['parts'], 'warnings': analysis.get('warnings') or [],
            'cautions': cautions, 'review_url': f'/review/{record.job.batch_id}/{job_id}',
            'notice': 'O arquivo mantém todas as suas datas; o mês é um filtro de documentos, não comprovação de cobertura.'})
        records[job_id] = record
        sources[job_id] = info['source']
    return {'items': items, 'snapshot': fingerprint([request.client_id, request.month, items, sources])}, records


@router.post('/package/preview', dependencies=[Depends(require_pro_browser_request)])
def preview_package(request: PackageRequest, owner=Depends(organization_owner), service=Depends(organization_service),
                    batches=Depends(get_conversion_batch_service), report=Depends(get_report_service),
                    async_report=Depends(get_async_conversion_report_service)):
    return package_snapshot(request, owner, service, batches, (async_report, report))[0]


def safe_name(value):
    stem = re.sub(r'[^A-Za-z0-9_-]+', '-', value.replace('\\', '/').split('/')[-1].rsplit('.', 1)[0]).strip('-')
    return stem[:60] or 'documento'


@router.post('/package/download', dependencies=[Depends(require_pro_browser_request)])
def download_package(request: PackageRequest, owner=Depends(organization_owner), service=Depends(organization_service),
                     batches=Depends(get_conversion_batch_service), store=Depends(get_conversion_batch_document_store),
                     report=Depends(get_report_service), async_report=Depends(get_async_conversion_report_service),
                     access=Depends(get_access_control_service)):
    reports = (async_report, report)
    snapshot, records = package_snapshot(request, owner, service, batches, reports)
    if not request.snapshot or snapshot['snapshot'] != request.snapshot:
        raise HTTPException(409, 'A seleção mudou. Confira novamente a prévia do pacote.')
    if any(not i['included'] or i.get('cautions') for i in snapshot['items']) and not request.accept_partial:
        raise HTTPException(409, 'Há exclusões ou ressalvas. Confira a prévia e confirme antes de baixar.')
    output, checks, total, included = BytesIO(), [], 0, 0
    manifest = {**snapshot, 'generated_at': datetime.now(timezone.utc).isoformat(), 'month': request.month, 'client_id': request.client_id,
                'notice': 'Verificação manual não é conciliação. Documentos exportados integralmente, sem recorte de movimentos.'}
    with ZipFile(output, 'w', compression=ZIP_DEFLATED) as archive:
        for index, item in enumerate(manifest['items'], 1):
            if not item['included']:
                continue
            record = records[item['job_id']]
            account_dir = 'conta-final-' + safe_name(item['account']) if item['account'] else 'conta-a-confirmar'
            period_dir = f'{item["start_date"]}_a_{item["end_date"]}' if item['start_date'] else 'periodo-a-confirmar'
            base = f'cliente/{account_dir}/{period_dir}/{index:02d}-{safe_name(item["filename"])}'
            files = []
            try:
                if item['bank_reviewed']:
                    for fmt in ('ofx', 'csv', 'xlsx'):
                        try:
                            data = export_canonical_record(record, owner, fmt, checks)
                            if data is None:
                                for candidate in reports:
                                    if candidate is None:
                                        continue
                                    try:
                                        candidate.assert_convert_owner(record.result.analysis_id, 'user', owner)
                                        path = candidate.get_convert_report_path(record.result.analysis_id, fmt)
                                        if path.stat().st_size > MAX_PACKAGE_BYTES:
                                            raise HTTPException(413, 'A seleção excede 64 MB. Gere pacotes menores.')
                                        data = path.read_bytes()
                                        break
                                    except AnalysisNotFoundError:
                                        continue
                            if data is not None:
                                files.append((f'{base}/movimentos.{fmt}', data))
                        except (ValueError, AnalysisNotFoundError):
                            if fmt != 'ofx':
                                raise
                        except HTTPException as exc:
                            if fmt != 'ofx' or exc.status_code != 400:
                                raise
                            item['cautions'].append('OFX indisponível para este documento; consulte as planilhas.')
                    if not any(name.endswith('.csv') for name, _ in files):
                        raise AnalysisNotFoundError
                raw = original_bytes(record, store)
                files.append((f'{base}/original.{record.job.document.file_type}', raw))
                if record.job.document.file_type == 'pdf':
                    for number, part in enumerate(item['parts'], 1):
                        if not part.get('requires_review'):
                            files.append((f'{base}/paginas-{number:02d}.pdf', part_bytes(raw, part)))
                if sum(len(data) for _, data in files) + total > MAX_PACKAGE_BYTES:
                    raise HTTPException(413, 'A seleção excede 64 MB. Gere pacotes menores.')
            except (OSError, ValueError, AnalysisNotFoundError):
                if not request.accept_partial:
                    raise HTTPException(409, 'Um arquivo não está disponível para esta entrega. Tente novamente.') from None
                item.update(included=False, reason=REASONS['missing'])
                continue
            item['files'] = [name for name, _ in files]
            for name, data in files:
                total += len(data)
                archive.writestr(name, data)
            included += 1
        if not included:
            raise HTTPException(409, 'Nenhum documento está pronto para esta entrega. Confira as pendências.')
        manifest_json = json.dumps(manifest, ensure_ascii=False, indent=2).encode('utf-8')
        csv_output = StringIO(newline='')
        fields = ['filename', 'document_type', 'included', 'reason', 'account', 'start_date', 'end_date',
                  'batch_id', 'version', 'cautions', 'notice']
        writer = csv.DictWriter(csv_output, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        # Neutralize spreadsheet formulas in untrusted file names/notes.
        writer.writerows({k: ("'" + str(v) if str(v).startswith(('=', '+', '-', '@', '\t', '\r')) else v)
                          for k, v in item.items()} for item in manifest['items'])
        manifest_csv = ('\ufeff' + csv_output.getvalue()).encode('utf-8')
        if total + len(manifest_json) + len(manifest_csv) > MAX_PACKAGE_BYTES:
            raise HTTPException(413, 'A seleção excede 64 MB. Gere pacotes menores.')
        archive.writestr('manifesto.json', manifest_json)
        archive.writestr('manifesto.csv', manifest_csv)
    validate_export_checks(checks)
    latest, _ = package_snapshot(request, owner, service, batches, reports)
    if latest['snapshot'] != snapshot['snapshot']:
        raise HTTPException(409, 'Um documento mudou durante a geração. Confira uma nova prévia do pacote.')
    from app.application.office_telemetry import office_event
    office_event(access, owner, 'office_package_generated')
    return Response(output.getvalue(), media_type='application/zip', headers={
        'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff',
        'Content-Disposition': 'attachment; filename="pacote-contabilidade.zip"'})
