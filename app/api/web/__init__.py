from fastapi import APIRouter

from app.api.web.views import router


def get_web_router() -> APIRouter:
    return router
