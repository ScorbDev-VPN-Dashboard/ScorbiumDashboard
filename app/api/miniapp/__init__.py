from fastapi import APIRouter
from .views import router as miniapp_router


def get_miniapp_router() -> APIRouter:
    r = APIRouter(prefix="/app")
    r.include_router(miniapp_router)
    return r
