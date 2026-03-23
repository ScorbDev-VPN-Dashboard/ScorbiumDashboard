from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from app.core.config import config
from app.api.v1 import get_router


class Server:
    def __init__(self) -> None:
        self.app = self._create_app()

    def _create_app(self) -> FastAPI:
        app = FastAPI(
            title=config.web.app_name,
            version=config.web.app_version,
            lifespan=self._lifespan,
        )

        # self._register_middlewares(app)
        self._register_routes(app)

        return app

    @staticmethod
    @asynccontextmanager
    async def _lifespan(app: FastAPI):
        print("Starting application...")

        yield
        print("Shutting down application...")

    # def _register_middlewares(self, app: FastAPI) -> None:
    #     app.add_middleware(
    #         CORSMiddleware,
    #         allow_origins=config.web.cors_origins,
    #         allow_credentials=True,
    #         allow_methods=["GET", "POST", "PUT", "DELETE"],
    #         allow_headers=["*"],
    #     )

    def _register_routes(self, app: FastAPI) -> None:
        app.include_router(get_router())
        
    @property
    def instance(self) -> FastAPI:
        return self.app