import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from app.core.config import config
from app.core.database import init_db, close_db
from app.api.v1 import get_router
from app.api.panel import get_panel_router
from app.api.middleware import RateLimitMiddleware
from app.utils.log import log

_bot = None
_dp = None
_bg_tasks = []


def get_bot():
    return _bot


def get_dp():
    return _dp


def _start_bg_task(coro, name: str = ""):
    """Start a background task, store reference, and log exceptions."""
    task = asyncio.create_task(coro, name=name or None)
    _bg_tasks.append(task)
    task.add_done_callback(lambda t: _bg_tasks.remove(t) if t in _bg_tasks else None)
    task.add_done_callback(_log_task_exception)
    return task


def _log_task_exception(task: asyncio.Task):
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        log.error(f"Background task {task.get_name() or task} failed: {exc}", exc_info=exc)


def _make_dp():
    """
    Build a fresh Dispatcher every time.
    Routers are module-level singletons in aiogram 3 — once attached they
    cannot be re-attached to a new Dispatcher.  The only safe approach is to
    re-import the handler modules so Python re-executes them and creates brand
    new Router objects.
    """
    import importlib
    import sys
    from aiogram import Dispatcher
    from app.bot.middlewares import BanCheckMiddleware
    from app.bot.middlewares.throttle import ThrottleMiddleware
    from app.bot.middlewares.channel_check import ChannelCheckMiddleware
    from app.bot.middlewares.user_notify import UserNotifyMiddleware

    handler_modules = [
        "app.bot.handlers.start",
        "app.bot.handlers.buy",
        "app.bot.handlers.my_keys",
        "app.bot.handlers.payments",
        "app.bot.handlers.admin",
        "app.bot.handlers.profile",
        "app.bot.handlers.features",
        "app.bot.handlers.language",
        "app.bot.handlers.trial",
    ]

    for mod_name in handler_modules:
        if mod_name in sys.modules:
            importlib.reload(sys.modules[mod_name])

    import app.bot.handlers.start as _start
    import app.bot.handlers.buy as _buy
    import app.bot.handlers.my_keys as _my_keys
    import app.bot.handlers.payments as _payments
    import app.bot.handlers.admin as _admin
    import app.bot.handlers.profile as _profile
    import app.bot.handlers.features as _features
    import app.bot.handlers.language as _language
    import app.bot.handlers.trial as _trial

    dp = Dispatcher()
    dp.update.outer_middleware(BanCheckMiddleware())
    dp.update.outer_middleware(ThrottleMiddleware())
    dp.update.outer_middleware(ChannelCheckMiddleware())
    dp.update.outer_middleware(UserNotifyMiddleware())
    dp.include_router(_start.router)
    dp.include_router(_buy.router)
    dp.include_router(_my_keys.router)
    dp.include_router(_payments.router)
    dp.include_router(_admin.router)
    dp.include_router(_profile.router)
    dp.include_router(_features.router)
    dp.include_router(_language.router)
    dp.include_router(_trial.router)
    return dp


@asynccontextmanager
async def _lifespan(app: FastAPI):
    global _bot, _dp

    log.info("🚀 Starting VPN Dashboard API...")
    await init_db()

    from aiogram import Bot
    from aiogram.enums import ParseMode
    from aiogram.client.default import DefaultBotProperties
    from app.tasks.payment_tasks import payment_polling_loop
    from app.tasks.vpn_tasks import expire_loop, sync_loop

    token = config.telegram.telegram_bot_token.get_secret_value()
    _bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    _dp = _make_dp()

    mode = config.telegram.telegram_type_protocol

    if mode == "webhook":
        await _bot.set_webhook(
            url=config.telegram.telegram_webhook_url,
            allowed_updates=_dp.resolve_used_update_types(),
            drop_pending_updates=True,
        )
        log.info(f"🤖 Bot webhook → {config.telegram.telegram_webhook_url}")
    else:
        await _bot.delete_webhook(drop_pending_updates=True)
        asyncio.create_task(
            _dp.start_polling(_bot, allowed_updates=_dp.resolve_used_update_types())
        )
        log.info("🤖 Bot polling started")

    _start_bg_task(payment_polling_loop(), name="payment_polling")
    _start_bg_task(expire_loop(), name="expire_loop")
    _start_bg_task(sync_loop(), name="sync_loop")

    import os as _os
    _env_cryptobot = _os.environ.get("CRYPTOBOT_TOKEN", "").strip()
    if _env_cryptobot:
        from app.core.database import AsyncSessionFactory as _ASF
        from app.services.bot_settings import BotSettingsService as _BSS
        async with _ASF() as _s:
            _existing = await _BSS(_s).get("cryptobot_token")
            if not _existing:
                await _BSS(_s).set("cryptobot_token", _env_cryptobot)
                await _s.commit()
                log.info("✅ CryptoBot token seeded from .env")

    log.info("✅ Application ready")

    from aiogram.types import BotCommand, BotCommandScopeAllPrivateChats, BotCommandScopeChat
    user_commands = [
        BotCommand(command="start",      description="🏠 Главное меню"),
        BotCommand(command="profile",    description="👤 Мой профиль"),
        BotCommand(command="keys",       description="🔑 Мои подписки"),
        BotCommand(command="status",     description="📊 Статус подписок"),
        BotCommand(command="extend",     description="🔄 Продлить подписку"),
        BotCommand(command="top",        description="🏆 Топ рефереров"),
        BotCommand(command="gift",       description="🎁 Подарить подписку"),
        BotCommand(command="autorenew",  description="🔄 Автопродление"),
        BotCommand(command="id",         description="🆔 Мой Telegram ID"),
    ]
    admin_commands = user_commands + [
        BotCommand(command="admin",      description="👑 Панель администратора"),
        BotCommand(command="ban",        description="🚫 Забанить пользователя"),
        BotCommand(command="unban",      description="✅ Разбанить пользователя"),
        BotCommand(command="promo",      description="🎁 Создать промокод"),
        BotCommand(command="addbalance", description="💰 Пополнить баланс"),
        BotCommand(command="givekey",    description="🔑 Выдать ключ"),
    ]
    try:
        await _bot.set_my_commands(user_commands, scope=BotCommandScopeAllPrivateChats())
        for admin_id in config.telegram.telegram_admin_ids:
            try:
                await _bot.set_my_commands(admin_commands, scope=BotCommandScopeChat(chat_id=admin_id))
            except Exception:
                pass
        log.info("✅ Bot commands set")
    except Exception as e:
        log.warning(f"Failed to set bot commands: {e}")
    yield

    log.info("🛑 Shutting down...")
    for task in list(_bg_tasks):
        if not task.done():
            task.cancel()
    if _bg_tasks:
        await asyncio.gather(*_bg_tasks, return_exceptions=True)
        _bg_tasks.clear()

    try:
        if mode == "webhook":
            await _bot.delete_webhook()
        else:
            await _dp.stop_polling()
    except Exception:
        pass
    try:
        await _bot.session.close()
    except Exception:
        pass
    await close_db()


def create_app() -> FastAPI:
    app = FastAPI(
        title=config.web.app_name,
        version=config.web.app_version,
        lifespan=_lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    origins = [str(o) for o in config.web.allowed_origins] or ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RateLimitMiddleware)

    @app.exception_handler(Exception)
    async def _global_exc(request: Request, exc: Exception) -> JSONResponse:
        log.error(f"Unhandled exception on {request.url}: {exc}")
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    @app.exception_handler(403)
    async def _forbidden_exc(request: Request, exc: Exception):
        from fastapi.templating import Jinja2Templates
        tpl = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))
        return tpl.TemplateResponse(
            "forbidden.html",
            {
                "request": request,
                "app_name": config.web.app_name,
                "app_version": config.web.app_version,
            },
            status_code=403,
        )

    from starlette.middleware.base import BaseHTTPMiddleware as _BHM
    class _SecurityHeaders(_BHM):
        async def dispatch(self, request: Request, call_next):
            resp = await call_next(request)
            resp.headers["X-Content-Type-Options"] = "nosniff"
            resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
            path = request.url.path
            if path.startswith("/panel"):
                resp.headers["X-Frame-Options"] = "SAMEORIGIN"
            else:
                resp.headers["X-Frame-Options"] = "DENY"
            return resp
    app.add_middleware(_SecurityHeaders)

    app.include_router(get_router())
    app.include_router(get_panel_router())

    from app.api.miniapp import get_miniapp_router
    app.include_router(get_miniapp_router())

    from app.api.web import get_web_router
    app.include_router(get_web_router())

    static_path = Path(__file__).resolve().parent.parent / "static"
    static_path.mkdir(exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(static_path)), name="static")

    @app.websocket("/ws/notifications", name="ws_notifications")
    async def ws_notifications(websocket: WebSocket):
        """Real-time notification stream for admin panel."""
        from app.services.notification import notification_manager
        from app.utils.security import decode_access_token_full

        token = websocket.query_params.get("token", "")
        info = decode_access_token_full(token) if token else None
        if not info:
            await websocket.close(code=4001)
            return

        await notification_manager.connect(websocket)
        try:
            while True:
                # Keep-alive ping-pong
                data = await websocket.receive_text()
                if data == "ping":
                    await websocket.send_text("pong")
        except Exception:
            pass
        finally:
            await notification_manager.disconnect(websocket)

    @app.post(config.telegram.telegram_webhook_path, include_in_schema=False)
    async def telegram_webhook(request: Request):
        from aiogram.types import Update
        bot, dp = get_bot(), get_dp()
        if bot is None or dp is None:
            return JSONResponse({"ok": False}, status_code=503)
        update = Update.model_validate(await request.json())
        await dp.feed_update(bot, update)
        return JSONResponse({"ok": True})

    @app.get("/panel-root", include_in_schema=False)
    async def panel_root():
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/panel/")

    return app
