import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.dependencies import get_analyze_service, get_report_service
from app.routers import (
    analyze_router,
    auth_router,
    client_router,
    contact_router,
    convert_router,
    health_router,
    reconcile_router,
    report_router,
)

app = FastAPI(title="gettdone API", version="0.1.0")


def get_cors_allow_origins() -> list[str]:
    configured_origins = os.getenv("CORS_ALLOW_ORIGINS", "").strip()
    if configured_origins:
        return [origin.strip() for origin in configured_origins.split(",") if origin.strip()]

    return [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]


app.add_middleware(
    CORSMiddleware,
    allow_origins=get_cors_allow_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(analyze_router)
app.include_router(convert_router)
app.include_router(auth_router)
app.include_router(client_router)
app.include_router(contact_router)
app.include_router(reconcile_router)
app.include_router(report_router)

frontend_dir = Path(__file__).resolve().parents[2] / "frontend"
if frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={
            "detail": "Validation failed",
            "errors": jsonable_encoder(exc.errors()),
        },
    )


__all__ = [
    "app",
    "get_analyze_service",
    "get_report_service",
    "get_cors_allow_origins",
]
