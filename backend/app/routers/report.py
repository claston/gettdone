from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from app.application import AnalysisNotFoundError, ReportService
from app.dependencies import get_report_service

router = APIRouter()


@router.get("/report/{analysis_id}")
def get_report(
    analysis_id: str,
    service: ReportService = Depends(get_report_service),
) -> FileResponse:
    try:
        report_path = service.get_report_path(analysis_id)
    except AnalysisNotFoundError:
        raise HTTPException(status_code=404, detail="Analysis not found")

    return FileResponse(
        path=report_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"gettdone_report_{analysis_id}.xlsx",
    )

