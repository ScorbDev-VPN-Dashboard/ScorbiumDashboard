import asyncio
import uvicorn

from app.core.config import config
from app.core.server import create_app
from app.utils.log import log


async def main() -> None:
    log.info("🚀 Starting Scorbium Dashboard VPN...")
    app = create_app()
    cfg = uvicorn.Config(
        app=app,
        host=config.web.server_host,
        port=config.web.server_port,
        log_level="info",
    )
    server = uvicorn.Server(cfg)
    await server.serve()


if __name__ == "__main__":
    asyncio.run(main())
