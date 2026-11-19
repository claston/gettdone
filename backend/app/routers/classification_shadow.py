"""Experimental PRO entry point; the classification core is channel-independent."""
import os
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.adapters.conversion.classification_shadow import ShadowTransport
from app.adapters.conversion.pro_fiscal_runtime import build_pro_fiscal_service
from app.application.classification_shadow.service import ShadowService
from app.routers.pro_fiscal import invoke
from app.routers.pro_organization import organization_owner
from app.routers.pro_security import require_pro_browser_request

router = APIRouter(prefix="/api/pro/financial-center/clients/{client_id}/classification-shadow", tags=["Classification shadow"])


def enabled_for(actor):
    return (os.getenv("CLASSIFICATION_SHADOW_MODE", "off") == "shadow"
            and actor in {item.strip() for item in os.getenv("CLASSIFICATION_SHADOW_USER_IDS", "").split(",") if item.strip()})


def shadow_owner(owner=Depends(organization_owner)):
    if not enabled_for(owner):
        raise HTTPException(404, "Avaliação experimental indisponível.")
    return owner


def shadow_service():
    configured = bool(os.getenv("CLASSIFICATION_SHADOW_BUCKET") and os.getenv("CLASSIFICATION_SHADOW_QUEUE_URL"))
    return ShadowService(build_pro_fiscal_service(storage=configured), ShadowTransport() if configured else None)


class Start(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_kind: Literal["movement", "invoice"]
    source_id: UUID
    source_version: int = Field(ge=1)


class Feedback(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_id: str = Field(min_length=1, max_length=50)
    verdict: Literal["agree", "disagree", "unknown"]
    comment: str = Field(default="", max_length=500)


@router.post("/runs", status_code=202, dependencies=[Depends(require_pro_browser_request)])
def start(client_id: str, request: Start, owner=Depends(shadow_owner), service=Depends(shadow_service)):
    return invoke(lambda: service.start(owner, client_id, **request.model_dump(mode="json")))


@router.get("/runs/{run_id}")
def read(client_id: str, run_id: UUID, owner=Depends(shadow_owner), service=Depends(shadow_service)):
    return invoke(lambda: service.read(owner, client_id, str(run_id)))


@router.post("/runs/{run_id}/feedback", dependencies=[Depends(require_pro_browser_request)])
def feedback(client_id: str, run_id: UUID, request: Feedback, owner=Depends(shadow_owner), service=Depends(shadow_service)):
    return invoke(lambda: service.evaluate(owner, client_id, str(run_id), **request.model_dump()))
