import os
import re

from fastapi import APIRouter, Response
from pydantic import BaseModel

router = APIRouter()


class PublicConfigResponse(BaseModel):
    whatsapp_support_number: str | None = None


@router.get("/public-config", response_model=PublicConfigResponse)
def public_config(response: Response) -> PublicConfigResponse:
    number = os.getenv("WHATSAPP_SUPPORT_NUMBER", "").strip()
    response.headers["Cache-Control"] = "no-store"
    return PublicConfigResponse(
        whatsapp_support_number=number if re.fullmatch(r"[1-9][0-9]{7,14}", number) else None,
    )
