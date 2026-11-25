"""Documentary review uses PRO authorization and existing canonical visibility."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.application.document_conference import DocumentConferenceService
from app.application.errors import AnalysisEditConflictError, AnalysisNotFoundError
from app.dependencies import get_async_conversion_report_service, get_conversion_batch_service, get_report_service
from app.routers.financial_center import financial_service
from app.routers.office_documents import current_record, owned_record
from app.routers.pro_fiscal import invoke
from app.routers.pro_organization import organization_owner
from app.routers.pro_security import require_pro_browser_request

router = APIRouter(prefix="/api/pro/documents/conference/{client_id}", tags=["Document conference"])


def conference_service(financial=Depends(financial_service), batches=Depends(get_conversion_batch_service),
                       report=Depends(get_report_service), async_report=Depends(get_async_conversion_report_service)):
    def resolve(owner, job_id):
        try:
            record = owned_record(batches, owner, job_id)
            # Canonical documents are bank sources, never supporting evidence.
            # Reject before opening another canonical connection while the
            # movement command already holds that owner's source row locks.
            if record.result and (record.result.payload or {}).get("authority") == "postgres_v1":
                raise ValueError("O extrato já é a origem do movimento. Selecione uma nota ou um comprovante para relacionar.")
            return current_record(record, owner, (async_report, report))
        except HTTPException as exc:
            if exc.status_code in {404, 410}:
                raise AnalysisNotFoundError from None
            if exc.status_code == 409:
                raise AnalysisEditConflictError from None
            raise
    return DocumentConferenceService(financial.repository, current_document=resolve)


class LinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str = Field(min_length=1, max_length=100)
    source: str = Field(pattern=r"^[a-f0-9]{64}$")
    movement_version: int = Field(ge=1)
    operation_id: str = Field(min_length=1, max_length=100)


class UndoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)
    operation_id: str = Field(min_length=1, max_length=100)


@router.get("/documents/{job_id}")
def document(client_id: str, job_id: str, owner=Depends(organization_owner), service=Depends(conference_service)):
    return invoke(lambda: service.document(owner, client_id, job_id))


@router.get("/movements/{movement_id}")
def links(client_id: str, movement_id: UUID, owner=Depends(organization_owner), service=Depends(conference_service)):
    return invoke(lambda: service.movement_links(owner, client_id, movement_id))


@router.post("/movements/{movement_id}/links", dependencies=[Depends(require_pro_browser_request)])
def link(client_id: str, movement_id: UUID, request: LinkRequest,
         owner=Depends(organization_owner), service=Depends(conference_service)):
    return invoke(lambda: service.link_document(owner, client_id, movement_id, **request.model_dump()))


@router.post("/movements/{movement_id}/links/{link_id}/undo", dependencies=[Depends(require_pro_browser_request)])
def unlink(client_id: str, movement_id: UUID, link_id: UUID, request: UndoRequest,
           owner=Depends(organization_owner), service=Depends(conference_service)):
    return invoke(lambda: service.unlink_document(owner, client_id, movement_id, link_id, **request.model_dump()))
