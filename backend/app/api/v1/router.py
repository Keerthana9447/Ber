from fastapi import APIRouter

from app.api.v1.auth import router as auth_router
from app.api.v1.customers import router as customers_router
from app.api.v1.dashboard import router as dashboard_router
from app.api.v1.health import router as health_router
from app.api.v1.invoices import router as invoices_router
from app.api.v1.notifications import router as notifications_router
from app.api.v1.payments import router as payments_router
from app.api.v1.products import router as products_router
from app.api.v1.settings import router as settings_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(auth_router, prefix="/auth")
api_router.include_router(customers_router)
api_router.include_router(dashboard_router)
api_router.include_router(notifications_router)
api_router.include_router(products_router)
api_router.include_router(settings_router)
api_router.include_router(invoices_router)
api_router.include_router(payments_router)
