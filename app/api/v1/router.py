from fastapi import APIRouter

from app.parcels.router import router as parcels_router
from app.payments.router import router as payments_router
from app.reports.router import router as reports_router

api_router = APIRouter()
api_router.include_router(parcels_router)
api_router.include_router(reports_router)
api_router.include_router(payments_router)
# app.layers has no router on purpose: it is reference data, not an API surface.
