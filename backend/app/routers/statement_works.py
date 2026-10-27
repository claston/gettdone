import csv
from datetime import date
from io import StringIO

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.application import AccessControlService
from app.application.conversion.conversion_batch_service import ConversionBatchService
from app.application.statement_works import StatementWorkService
from app.dependencies import get_access_control_service, get_conversion_batch_service
from app.routers.pro_organization import organization_errors, organization_owner
from app.routers.pro_security import require_pro_browser_request
from app.security_baseline import is_production_env, read_bool_env

router = APIRouter(prefix="/api/pro/works", tags=["Statement works"])


def work_owner(owner: str = Depends(organization_owner)):
    if not read_bool_env("PRO_STATEMENT_WORKS_ENABLED", default=not is_production_env()):
        raise HTTPException(503, "Trabalhos ainda não estão disponíveis. Conversões continuam acessíveis.")
    return owner


def work_service(
    access: AccessControlService = Depends(get_access_control_service),
    batches: ConversionBatchService | None = Depends(get_conversion_batch_service),
):
    if batches is None:
        raise HTTPException(503, "Não foi possível carregar os extratos agora.")
    return StatementWorkService(access.db, batches.repository)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class CreateWork(Strict):
    client_id: str = Field(min_length=1, max_length=100)
    period_id: str = Field(min_length=1, max_length=100)


class Version(Strict):
    expected_version: int = Field(ge=1)


class Scope(Version):
    account_ids: list[str] = Field(min_length=1, max_length=100)


class LinkDocument(Version):
    job_id: str = Field(min_length=1, max_length=100)
    account_id: str | None = Field(default=None, max_length=100)
    context_version: int = Field(ge=0)


class Evidence(Version):
    identity_confirmed: bool
    start_date: date | None = None
    end_date: date | None = None
    empty_confirmed: bool = False
    balance: float | None = None

    @model_validator(mode="after")
    def dates(self):
        if bool(self.start_date) != bool(self.end_date) or self.start_date and self.start_date > self.end_date:
            raise ValueError("Informe as duas datas do documento em ordem válida.")
        return self


class Reason(Version):
    reason: str = Field(min_length=3, max_length=2000)


class Exclusion(Reason):
    excluded: bool = True


class Completion(Version):
    expected_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    remarks: dict[str, str] = Field(default_factory=dict, max_length=101)

    @model_validator(mode="after")
    def remark_lengths(self):
        if any(len(k) > 100 or len(v) > 2000 for k, v in self.remarks.items()):
            raise ValueError("Confira os limites das ressalvas.")
        return self


@router.get("")
def list_works(
    owner: str = Depends(work_owner),
    service=Depends(work_service),
    client_id: str | None = Query(None, max_length=100),
    period_id: str | None = Query(None, max_length=100),
    state: str | None = Query(None, pattern="^(open|completed)$"),
    offset: int = Query(0, ge=0, le=100000),
):
    with organization_errors():
        return service.list(owner, client_id=client_id, period_id=period_id, state=state, offset=offset)


@router.post("", dependencies=[Depends(require_pro_browser_request)])
def create_work(request: CreateWork, owner: str = Depends(work_owner), service=Depends(work_service)):
    with organization_errors():
        return service.create(owner, request.client_id, request.period_id)


@router.get("/{work_id}")
def get_work(work_id: str, owner: str = Depends(work_owner), service=Depends(work_service)):
    with organization_errors():
        return service.get(owner, work_id)


@router.put("/{work_id}/scope", dependencies=[Depends(require_pro_browser_request)])
def save_scope(work_id: str, request: Scope, owner: str = Depends(work_owner), service=Depends(work_service)):
    with organization_errors():
        return service.scope(owner, work_id, request.expected_version, request.account_ids)


@router.post("/{work_id}/documents", dependencies=[Depends(require_pro_browser_request)])
def link_document(work_id: str, request: LinkDocument, owner: str = Depends(work_owner), service=Depends(work_service)):
    with organization_errors():
        return service.attach(owner, work_id, request.expected_version, request.job_id, request.account_id, request.context_version)


@router.post("/{work_id}/batches/{batch_id}", dependencies=[Depends(require_pro_browser_request)])
def link_batch(work_id: str, batch_id: str, request: Version, owner: str = Depends(work_owner), service=Depends(work_service)):
    with organization_errors():
        return service.attach_batch(owner, work_id, request.expected_version, batch_id)


@router.put("/{work_id}/documents/{job_id}/evidence", dependencies=[Depends(require_pro_browser_request)])
def save_evidence(work_id: str, job_id: str, request: Evidence, owner: str = Depends(work_owner), service=Depends(work_service)):
    with organization_errors():
        return service.annotate(
            owner, work_id, request.expected_version, "evidence", job_id, request.model_dump(mode="json", exclude={"expected_version"})
        )


@router.put("/{work_id}/documents/{job_id}/exclusion", dependencies=[Depends(require_pro_browser_request)])
def exclude_document(work_id: str, job_id: str, request: Exclusion, owner: str = Depends(work_owner), service=Depends(work_service)):
    with organization_errors():
        reason = request.reason.strip()
        if len(reason) < 3:
            raise ValueError("Justifique a exclusão ou restauração do documento.")
        return service.annotate(
            owner, work_id, request.expected_version, "exclusions", job_id, {"reason": reason} if request.excluded else None
        )


@router.put("/{work_id}/duplicates/{group_id}", dependencies=[Depends(require_pro_browser_request)])
def resolve_duplicate(work_id: str, group_id: str, request: Reason, owner: str = Depends(work_owner), service=Depends(work_service)):
    with organization_errors():
        if len(request.reason.strip()) < 3:
            raise ValueError("Justifique os movimentos distintos após conferência.")
        return service.annotate(
            owner, work_id, request.expected_version, "duplicate_resolutions", group_id, {"reason": request.reason.strip()}
        )


@router.post("/{work_id}/complete", dependencies=[Depends(require_pro_browser_request)])
def complete_work(work_id: str, request: Completion, owner: str = Depends(work_owner), service=Depends(work_service)):
    with organization_errors():
        return service.complete(owner, work_id, request.expected_version, request.expected_fingerprint, request.remarks)


@router.post("/{work_id}/reopen", dependencies=[Depends(require_pro_browser_request)])
def reopen_work(work_id: str, request: Reason, owner: str = Depends(work_owner), service=Depends(work_service)):
    with organization_errors():
        if len(request.reason.strip()) < 3:
            raise ValueError("Informe o motivo da reabertura.")
        return service.reopen(owner, work_id, request.expected_version, request.reason.strip())


@router.get("/{work_id}/export")
def export_work(work_id: str, owner: str = Depends(work_owner), service=Depends(work_service)):
    with organization_errors():
        work = service.get(owner, work_id)
    if work["state"] != "completed" or work["needs_reassessment"]:
        raise HTTPException(409, "Conclua uma versão atual do trabalho antes de exportar o recorte.")
    if work["assessment"]["blockers"]:
        raise HTTPException(409, "Os documentos atuais não permitem exportar o fechamento.")
    output = StringIO(newline="")
    writer = csv.writer(output, delimiter=";")
    writer.writerow(["conta", "data", "descrição", "valor", "conversão", "lançamento", "resultado"])
    conclusion = "Concluído com ressalvas" if work["closings"][0]["snapshot"]["conclusion"] == "with_reservations" else "Concluído"

    def safe_cell(value):
        text = str(value)
        return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")) else text

    for row in work["assessment"]["movements"]:
        writer.writerow(
            [
                safe_cell(row["account_id"]),
                row["date"],
                safe_cell(row["description"]),
                f"{row['amount']:.2f}",
                safe_cell(row["job_id"]),
                safe_cell(row["transaction_id"]),
                conclusion,
            ]
        )
    return Response(
        "\ufeff" + output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="fechamento-{work_id}.csv"'},
    )
