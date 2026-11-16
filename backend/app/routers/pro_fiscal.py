"""Paid PRO fiscal workspace. No fiscal capabilities are added to Free routes."""

import csv
from io import StringIO
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from app.adapters.conversion.pro_fiscal_runtime import build_pro_fiscal_service
from app.application.errors import AnalysisAccessDeniedError, AnalysisEditConflictError, AnalysisNotFoundError
from app.routers.pro_organization import organization_owner
from app.routers.pro_security import require_pro_browser_request
from app.security_baseline import read_bool_env

router = APIRouter(prefix="/api/pro/financial-center/clients/{client_id}", tags=["PRO fiscal"])


def features():
    return {
        "invoices": read_bool_env("PRO_FISCAL_ENABLED", default=False),
        "taxes": read_bool_env("PRO_TAX_ENABLED", default=False),
        "revenue": read_bool_env("PRO_REVENUE_ENABLED", default=False),
    }


def fiscal_service():
    if not any(features().values()):
        raise HTTPException(503, "A gestão fiscal ainda não está habilitada.")
    try:
        return build_pro_fiscal_service(storage=features()["invoices"])
    except (KeyError, ValueError):
        raise HTTPException(503, "A gestão fiscal está indisponível neste ambiente.") from None


def visible_workspace(result):
    flags = features()
    result["features"] = flags
    if not flags["invoices"]:
        for key in ("documents", "imports", "links", "suggestions"):
            result[key] = []
        result["summary"]["documented_sales"] = None
        result["summary"]["pending_documents"] = 0
    if not flags["taxes"]:
        result["summary"]["tax_payments"] = {}
        for row in result["movements"]:
            row["tax_suggestion"] = None
    if not flags["revenue"]:
        for key in ("operating_receipts", "unclassified_inflows", "excluded_inflows"):
            result["summary"][key] = None
    return result


def invoke(action, *, feature=None):
    if feature and not features()[feature]:
        raise HTTPException(503, "Esta funcionalidade ainda não está habilitada.")
    try:
        return action()
    except AnalysisNotFoundError:
        raise HTTPException(404, "Documento, movimento ou empresa não disponível.") from None
    except AnalysisAccessDeniedError:
        raise HTTPException(403, "Esta funcionalidade exige acesso ativo ao PRO.") from None
    except AnalysisEditConflictError:
        raise HTTPException(409, "Os dados mudaram. Atualize e confira antes de confirmar.") from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    except Exception:
        raise HTTPException(503, "Não foi possível concluir a operação. Tente novamente.") from None


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ImportRequest(Strict):
    filename: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(ge=1, le=5 * 1024 * 1024)
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")


class Command(Strict):
    operation_id: str = Field(min_length=1, max_length=100)
    expected_version: int = Field(ge=0)


class ClassificationRequest(Command):
    movement_version: int = Field(ge=1)
    category: str = Field(max_length=60)
    details: dict[str, str] = Field(default_factory=dict)


class ReviewRequest(Command):
    changes: dict[str, str | None] = Field(default_factory=dict)


class LinkRequest(Strict):
    operation_id: str = Field(min_length=1, max_length=100)
    document_id: UUID
    movement_id: UUID
    document_version: int = Field(ge=1)
    movement_version: int = Field(ge=1)
    amount: str = Field(pattern=r"^\d{1,16}(\.\d{1,2})?$")


@router.get("/fiscal")
def workspace(
    client_id: str,
    month: str = Query(pattern=r"^20\d{2}-(0[1-9]|1[0-2])$"),
    account: str | None = None,
    offset: int = Query(default=0, ge=0, le=10000),
    document_id: UUID | None = None,
    movement_id: UUID | None = None,
    owner=Depends(organization_owner),
    service=Depends(fiscal_service),
):
    result = invoke(
        lambda: service.workspace(
            owner,
            client_id,
            month=month,
            account=account,
            offset=offset,
            document_id=str(document_id) if document_id else None,
            movement_id=str(movement_id) if movement_id else None,
        )
    )
    return visible_workspace(result)


@router.post("/fiscal/imports", status_code=201, dependencies=[Depends(require_pro_browser_request)])
def prepare(client_id: str, request: ImportRequest, owner=Depends(organization_owner), service=Depends(fiscal_service)):
    return invoke(lambda: service.prepare_import(owner, client_id, **request.model_dump()), feature="invoices")


@router.post("/fiscal/imports/{import_id}/submit", status_code=202, dependencies=[Depends(require_pro_browser_request)])
def submit(client_id: str, import_id: UUID, owner=Depends(organization_owner), service=Depends(fiscal_service)):
    return invoke(lambda: service.submit_import(owner, client_id, import_id), feature="invoices")


@router.post("/fiscal/documents/{document_id}/review", dependencies=[Depends(require_pro_browser_request)])
def review(client_id: str, document_id: UUID, request: ReviewRequest, owner=Depends(organization_owner), service=Depends(fiscal_service)):
    return invoke(lambda: service.review_document(owner, client_id, document_id, **request.model_dump()), feature="invoices")


@router.post("/fiscal/links", dependencies=[Depends(require_pro_browser_request)])
def link(client_id: str, request: LinkRequest, owner=Depends(organization_owner), service=Depends(fiscal_service)):
    return invoke(lambda: service.link(owner, client_id, **request.model_dump()), feature="invoices")


@router.post("/fiscal/links/{link_id}/undo", dependencies=[Depends(require_pro_browser_request)])
def unlink(client_id: str, link_id: UUID, request: Command, owner=Depends(organization_owner), service=Depends(fiscal_service)):
    return invoke(lambda: service.unlink(owner, client_id, link_id, **request.model_dump()), feature="invoices")


@router.post("/classifications/{movement_id}", dependencies=[Depends(require_pro_browser_request)])
def classify(
    client_id: str, movement_id: UUID, request: ClassificationRequest, owner=Depends(organization_owner), service=Depends(fiscal_service)
):
    feature = "taxes" if request.category.startswith("tax_") or (request.category == "unclassified" and features()["taxes"]) else "revenue"
    return invoke(lambda: service.classify(owner, client_id, movement_id, **request.model_dump()), feature=feature)


@router.get("/fiscal/documents/{document_id}/source")
def source(client_id: str, document_id: UUID, owner=Depends(organization_owner), service=Depends(fiscal_service)):
    raw, ref = invoke(lambda: service.source(owner, client_id, document_id), feature="invoices")
    return Response(
        raw,
        media_type="application/pdf" if ref["file_type"] == "pdf" else "application/xml",
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f'attachment; filename="nota.{ref["file_type"]}"',
        },
    )


@router.get("/fiscal/export")
def export(
    client_id: str,
    month: str = Query(pattern=r"^20\d{2}-(0[1-9]|1[0-2])$"),
    account: str | None = None,
    owner=Depends(organization_owner),
    service=Depends(fiscal_service),
):
    result = visible_workspace(invoke(lambda: service.workspace(owner, client_id, month=month, account=account, all_movements=True)))
    if result["truncated"]:
        raise HTTPException(400, "O recorte excede 10.000 movimentos. Filtre uma conta antes de exportar.")
    stream = StringIO()
    writer = csv.writer(stream, delimiter=";")
    writer.writerow(["Tipo", "Identificador", "Data", "Descrição", "Valor", "Classificação / situação", "Escopo"])

    def safe(value):
        text = str(value or "")
        return "'" + text if text.startswith(("=", "+", "-", "@", "\t", "\r")) else text

    for note in result["documents"]:
        writer.writerow(
            [
                "nota",
                note["id"],
                note["issued_on"],
                safe(note["number"]),
                note["gross_amount"],
                "review_required" if note["review_required"] else note["status"],
                "Notas acessíveis",
            ]
        )
    for row in result["movements"]:
        writer.writerow(
            [
                "movimento",
                row["id"],
                row["date"],
                safe(row["description"]),
                row["amount"],
                (
                    (row["classification"] or {}).get("category", "unclassified")
                    + " / "
                    + (row["classification"] or {}).get("state", "unclassified")
                ),
                "Movimentos acessíveis do recorte",
            ]
        )
    for item in result["links"]:
        writer.writerow(
            [
                "vínculo",
                item["id"],
                "",
                item["document_id"] + " / " + item["movement_id"],
                item["amount"],
                item["state"],
                "Vínculos acessíveis",
            ]
        )
    return Response(
        "\ufeff" + stream.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Cache-Control": "private, no-store", "Content-Disposition": 'attachment; filename="fiscal.csv"'},
    )
