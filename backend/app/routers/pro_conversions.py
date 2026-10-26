"""The converter's file history works without organization tables or APIs."""

from fastapi import APIRouter, Depends, HTTPException, Query

from app.application import AccessControlService
from app.application.conversion.conversion_batch_service import ConversionBatchService
from app.application.pro_organization import OrganizationService
from app.dependencies import get_access_control_service, get_conversion_batch_service
from app.routers.pro_organization import conversion_owner, conversion_page, organization_enabled

router = APIRouter(prefix="/api/pro/conversions", tags=["PRO conversions"])


@router.get("")
def list_files(
    owner: str = Depends(conversion_owner),
    batches: ConversionBatchService | None = Depends(get_conversion_batch_service),
    access: AccessControlService = Depends(get_access_control_service),
    cursor: str | None = Query(default=None, max_length=500),
    limit: int = Query(default=30, ge=1, le=50),
    include_organization: bool = False,
    client_id: str | None = Query(default=None, max_length=100),
    period_id: str | None = Query(default=None, max_length=100),
    account_id: str | None = Query(default=None, max_length=100),
    unassigned: bool = False,
) -> dict:
    enabled = organization_enabled()
    filtered = any((client_id, period_id, account_id, unassigned))
    if filtered and (not include_organization or not enabled):
        raise HTTPException(403, "Os filtros de organização não estão disponíveis.")
    service = OrganizationService(access.db) if include_organization and enabled else None
    return {**conversion_page(owner, batches, service, cursor, limit, client_id, period_id, account_id, unassigned),
            "organization_available": enabled}
