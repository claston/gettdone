from app.routers.admin_auth import router as admin_auth_router
from app.routers.analyze import router as analyze_router
from app.routers.auth import router as auth_router
from app.routers.banks import router as banks_router
from app.routers.checkout import router as checkout_router
from app.routers.client import router as client_router
from app.routers.contact import router as contact_router
from app.routers.health import router as health_router
from app.routers.plans import router as plans_router
from app.routers.reconcile import router as reconcile_router
from app.routers.report import router as report_router
from app.routers.upload import router as convert_router

__all__ = [
    "analyze_router",
    "admin_auth_router",
    "auth_router",
    "banks_router",
    "checkout_router",
    "client_router",
    "contact_router",
    "convert_router",
    "health_router",
    "plans_router",
    "reconcile_router",
    "report_router",
]
