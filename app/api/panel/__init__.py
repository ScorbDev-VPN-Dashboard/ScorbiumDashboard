from fastapi import APIRouter
from .views import router as views_router


def get_panel_router() -> APIRouter:
    panel = APIRouter(prefix="/panel")
    panel.include_router(views_router)
    return panel
