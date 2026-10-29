from datetime import datetime, timezone
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from app.application import AccessControlService
from app.application.conversion.conversion_batch_service import ConversionBatchService
from app.application.received_statements import ReceivedStatements
from app.application.report_service import ReportService
from app.dependencies import (
    get_access_control_service,
    get_async_conversion_report_service,
    get_conversion_batch_service,
    get_report_service,
)
from app.routers.conversion_batches import _find_convert_report, _unique_export_name
from app.routers.pro_organization import organization_errors, organization_owner
from app.routers.pro_security import require_pro_browser_request

router = APIRouter(prefix="/api/pro/receipts", tags=["Received statements"])


def receipt_service(access: AccessControlService = Depends(get_access_control_service),
                    batches: ConversionBatchService | None = Depends(get_conversion_batch_service)):
    if batches is None:
        raise HTTPException(503, "Os extratos não estão disponíveis agora. Tente novamente.")
    return ReceivedStatements(access.db, batches.repository)


class Confirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=0)
    name: str = Field(min_length=1, max_length=160)
    tax_id: str | None = Field(default=None, max_length=30)
    client_id: str | None = Field(default=None, max_length=100)


class ClientEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=160)
    tax_id: str | None = Field(default=None, max_length=30)
    expected_updated_at: str = Field(min_length=1, max_length=100)


@router.get("")
def dashboard(owner: str = Depends(organization_owner), service=Depends(receipt_service),
              cursor: str = Query("", max_length=100), pending_cursor: str = Query("", max_length=100)):
    return service.dashboard(owner, after=cursor, pending_after=pending_cursor)


@router.post("/refresh", dependencies=[Depends(require_pro_browser_request)])
def refresh(owner: str = Depends(organization_owner), service=Depends(receipt_service),
            cursor: str | None = Query(None, max_length=500)):
    # Explicit bounded ingestion of existing jobs; viewing the dashboard never reconverts a PDF.
    return {"next_cursor": service.sync(owner, cursor)}


def live_record(service, owner, document):
    record = service.repository.get(document["job_id"])
    if (not record or record.job.batch_id != document["batch_id"] or record.job.identity.identity_type != "user"
            or record.job.identity.identity_id != owner or record.expires_at <= datetime.now(timezone.utc)
            or datetime.fromisoformat(document["expires_at"]) <= datetime.now(timezone.utc)
            or str(getattr(record.status, "value", record.status)) == "expired"):
        return None
    return record


@router.post("/documents/{job_id}/confirm", dependencies=[Depends(require_pro_browser_request)])
def confirm(job_id: str, request: Confirmation, owner: str = Depends(organization_owner), service=Depends(receipt_service)):
    record = service.repository.get(job_id)
    if (not record or record.job.identity.identity_type != "user" or record.job.identity.identity_id != owner):
        raise HTTPException(404, "Extrato não encontrado.")
    if record.expires_at <= datetime.now(timezone.utc):
        raise HTTPException(410, "O arquivo expirou.")
    with organization_errors():
        return service.confirm(owner, job_id, **request.model_dump())


@router.patch("/clients/{client_id}", dependencies=[Depends(require_pro_browser_request)])
def edit_client(client_id: str, request: ClientEdit, owner: str = Depends(organization_owner), service=Depends(receipt_service)):
    with organization_errors():
        return service.edit_client(owner, client_id, **request.model_dump())


@router.get("/clients/{client_id}/workspace")
def workspace(client_id: str, owner: str = Depends(organization_owner), service=Depends(receipt_service),
              cursor: str = Query("", max_length=100)):
    with organization_errors():
        documents = service.documents(owner, client_id, after=cursor, limit=51)
        with service.db.connect() as conn:
            client = service._entity(conn, "pro_clients", service._organization(conn, owner), client_id)
        jobs = []
        for document in documents[:50]:
            record = live_record(service, owner, document)
            if not record:
                continue
            jobs.append({"job_id": record.job.job_id, "source_batch_id": record.job.batch_id,
                         "filename": record.job.document.filename,
                         "expires_at": min(document["expires_at"], record.expires_at.isoformat()),
                         "status": getattr(record.status, "value", record.status),
                         "analysis_id": record.result.analysis_id if record.result else None,
                         "error_code": record.failure.code if record.failure else None,
                         "result": record.result.payload if record.result else None})
        return {"batch_id": client_id, "client_name": client["name"], "status": "completed", "jobs": jobs,
                "next_cursor": documents[49]["job_id"] if len(documents) > 50 else None}


@router.get("/clients/{client_id}/export")
def export_client(client_id: str, owner: str = Depends(organization_owner), service=Depends(receipt_service),
                  report: ReportService = Depends(get_report_service),
                  async_report: ReportService | None = Depends(get_async_conversion_report_service)):
    with organization_errors():
        documents = service.documents(owner, client_id)
    buffer, exported, used = BytesIO(), 0, set()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
        for document in documents:
            record = live_record(service, owner, document)
            if not record or not record.result:
                continue
            analysis = (record.result.payload or {}).get("analysis") or {}
            file_format = "xlsx" if analysis.get("financial_document") is not None else "ofx"
            path = _find_convert_report(record.result.analysis_id,
                                        candidates=[r for r in (async_report, report) if r], file_format=file_format)
            if path is None:
                continue
            name = _unique_export_name(filename=record.job.document.filename, analysis_id=record.result.analysis_id,
                                       used_names=used, file_format=file_format)
            archive.writestr(name, path.read_bytes())
            exported += 1
    if not exported:
        raise HTTPException(409, "Nenhum extrato disponível para exportação.")
    return Response(buffer.getvalue(), media_type="application/zip", headers={
        "Cache-Control": "private, no-store", "Content-Disposition": f'attachment; filename="extratos-{client_id}.zip"',
        "X-Conversion-Count": str(exported), "X-Content-Type-Options": "nosniff"})
