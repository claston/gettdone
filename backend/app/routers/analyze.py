from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.application import AnalyzeService, InvalidFileContentError, UnsupportedFileTypeError
from app.dependencies import get_analyze_service
from app.schemas import AnalyzeResponse

router = APIRouter()


@router.post("/analyze", response_model=AnalyzeResponse)
async def analyze(
    file: UploadFile = File(...),
    service: AnalyzeService = Depends(get_analyze_service),
) -> AnalyzeResponse:
    try:
        data = await file.read()
        return service.analyze(filename=file.filename or "", raw_bytes=data)
    except UnsupportedFileTypeError:
        raise HTTPException(status_code=400, detail="Unsupported file type. Use CSV, XLSX, OFX, or PDF.")
    except InvalidFileContentError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
