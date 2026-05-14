from fastapi import APIRouter

from app.application.bank_catalog import list_bank_options

router = APIRouter()


@router.get("/banks")
def get_banks() -> dict[str, list[dict[str, object]]]:
    return {"banks": list_bank_options()}
