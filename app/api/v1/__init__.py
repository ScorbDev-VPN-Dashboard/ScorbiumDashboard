from fastapi import APIRouter
from .healthy import router as healthy

def get_router() -> APIRouter:
    api_router = APIRouter(prefix="/api/v1")

    api_router.include_router(
        healthy,
        prefix="/health",
        tags=["Health"],
    )

    return api_router