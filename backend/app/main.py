import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.dependencies import close_access_control_service, get_analyze_service, get_report_service
from app.routers import (
    admin_auth_router,
    analyze_router,
    auth_router,
    checkout_router,
    client_router,
    contact_router,
    convert_router,
    health_router,
    plans_router,
    reconcile_router,
    report_router,
)
from app.security_baseline import (
    is_production_env,
    parse_cors_allow_origins,
    read_bool_env,
    validate_production_security_baseline,
)


def is_api_docs_enabled() -> bool:
    default = not is_production_env()
    return read_bool_env("ENABLE_API_DOCS", default=default)


validate_production_security_baseline()


app = FastAPI(
    title="OFX Simples API",
    version="0.1.0",
    docs_url="/docs" if is_api_docs_enabled() else None,
    redoc_url="/redoc" if is_api_docs_enabled() else None,
    openapi_url="/openapi.json" if is_api_docs_enabled() else None,
)


@app.on_event("shutdown")
def _shutdown_services() -> None:
    close_access_control_service()


def get_cors_allow_origins() -> list[str]:
    configured_origins = os.getenv("CORS_ALLOW_ORIGINS", "").strip()
    if configured_origins:
        return parse_cors_allow_origins(configured_origins)

    if is_production_env():
        raise RuntimeError("CORS_ALLOW_ORIGINS must be configured when APP_ENV=production.")

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
app.include_router(plans_router)
app.include_router(admin_auth_router)
app.include_router(analyze_router)
app.include_router(convert_router)
app.include_router(auth_router)
app.include_router(client_router)
app.include_router(checkout_router)
app.include_router(contact_router)
app.include_router(reconcile_router)
app.include_router(report_router)

frontend_dir = Path(__file__).resolve().parents[2] / "frontend"
if frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    if is_production_env():
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


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
