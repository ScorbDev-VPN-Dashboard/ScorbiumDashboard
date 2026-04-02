import asyncio
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from app.core.config import config
from app.core.database import init_db, close_db
from app.bot.handlers import start, buy, my_keys, payments, admin
from app.bot.handlers import language as language_handler
from app.bot.middlewares import BanCheckMiddleware
from app.bot.middlewares.throttle import ThrottleMiddleware
from app.bot.middlewares.channel_check import ChannelCheckMiddleware
from app.utils.log import log


async def start_bot() -> None:
    """Start bot in long-polling mode (standalone)."""
    await init_db()

    token = config.telegram.telegram_bot_token.get_secret_value()
    bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()

    dp.update.outer_middleware(BanCheckMiddleware())
    dp.update.outer_middleware(ThrottleMiddleware())
    dp.update.outer_middleware(ChannelCheckMiddleware())

    dp.include_router(start.router)
    dp.include_router(buy.router)
    dp.include_router(my_keys.router)
    dp.include_router(payments.router)
    dp.include_router(admin.router)
    dp.include_router(language_handler.router)

    log.info("🤖 Bot started (standalone long polling)")
    try:
        await bot.delete_webhook(drop_pending_updates=True)

        from aiogram.types import BotCommand, BotCommandScopeAllPrivateChats
        user_commands = [
            BotCommand(command="start",   description="🏠 Главное меню"),
            BotCommand(command="profile", description="👤 Мой профиль"),
            BotCommand(command="keys",    description="🔑 Мои подписки"),
            BotCommand(command="status",  description="📊 Статус подписок"),
            BotCommand(command="top",     description="🏆 Топ рефереров"),
            BotCommand(command="id",      description="🆔 Мой Telegram ID"),
        ]
        await bot.set_my_commands(user_commands, scope=BotCommandScopeAllPrivateChats())

        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()
        await close_db()


if __name__ == "__main__":
    asyncio.run(start_bot())
