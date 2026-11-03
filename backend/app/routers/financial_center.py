"""Read-only financial center; no command or export endpoints."""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query

from app.application.errors import AnalysisAccessDeniedError, AnalysisEditConflictError, AnalysisNotFoundError
from app.application.financial_center import FinancialCenterService
from app.dependencies import get_canonical_pro_service
from app.routers.pro_organization import organization_owner
from app.security_baseline import read_bool_env

router = APIRouter(prefix="/api/pro/financial-center", tags=["Financial center"])


def financial_service():
    if not read_bool_env("PRO_FINANCIAL_CENTER_ENABLED", default=False):
        raise HTTPException(503, "A Central Financeira ainda não está habilitada. Seus arquivos continuam no conversor.")
    canonical = get_canonical_pro_service()
    if canonical is None:
        raise HTTPException(503, "A Central Financeira está indisponível. Tente novamente.")
    return FinancialCenterService(canonical.repository)


@router.get("/clients/{client_id}")
def month(client_id: str, month: str = Query(pattern=r"^20\d{2}-(0[1-9]|1[0-2])$"),
          account: list[str] = Query(default=[], max_length=50), day: int | None = Query(default=None, ge=1, le=31),
          pending: bool = False, limit: int = Query(default=50, ge=1, le=100), cursor: str | None = Query(default=None, max_length=200),
          owner: str = Depends(organization_owner), service=Depends(financial_service)):
    try:
        return service.month(owner, client_id, month=date.fromisoformat(month+"-01"), accounts=account,
                             day=day, pending=pending, limit=limit, cursor=cursor)
    except AnalysisNotFoundError:
        raise HTTPException(404, "Cliente ou conta não disponível.") from None
    except AnalysisAccessDeniedError:
        raise HTTPException(403, "A consulta exige acesso ativo ao PRO. A degustação ainda não dispõe de movimentos canônicos.") from None
    except AnalysisEditConflictError:
        raise HTTPException(409, "O acesso aos documentos mudou. Atualize a consulta.") from None
    except ValueError:
        raise HTTPException(400, "Confira o mês, o dia e a paginação da consulta.") from None
    except Exception:
        # Do not log financial queries, descriptions, identities or DB payloads.
        raise HTTPException(503, "Não foi possível consultar os movimentos. Tente novamente.") from None
