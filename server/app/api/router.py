from fastapi import APIRouter

from app.api.routes.alerts import router as alerts_router
from app.api.routes.exports import router as exports_router
from app.api.routes.fares import router as fares_router
from app.api.routes.health import router as health_router
from app.api.routes.identity import router as identity_router
from app.api.routes.notifications import router as notifications_router
from app.api.routes.realtime import router as realtime_router
from app.api.routes.subscriptions import router as subscriptions_router

api_router = APIRouter()
api_router.include_router(health_router, prefix="/health", tags=["health"])
api_router.include_router(identity_router, prefix="/auth", tags=["identity"])
api_router.include_router(fares_router, tags=["fares"])
api_router.include_router(notifications_router, prefix="/notifications", tags=["notifications"])
api_router.include_router(alerts_router, tags=["alerts"])
api_router.include_router(exports_router, prefix="/exports", tags=["exports"])
api_router.include_router(realtime_router, prefix="/realtime", tags=["realtime"])
api_router.include_router(
    subscriptions_router,
    prefix="/subscriptions",
    tags=["subscriptions"],
)
