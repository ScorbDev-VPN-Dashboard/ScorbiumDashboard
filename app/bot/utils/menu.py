"""Утилита для получения главного меню с настройками из БД."""
import json
from aiogram.types import InlineKeyboardMarkup
from app.bot.keyboards.main import main_menu_kb, _DEFAULT_LAYOUT
from app.services.bot_settings import BotSettingsService

_BUTTON_IDS = [
    "my_keys", "buy", "profile", "balance", "promo", "support",
    "connect", "about", "servers", "top_referrers", "status", "language",
]


async def get_main_menu_kb(session) -> InlineKeyboardMarkup:
    s = await BotSettingsService(session).get_all()

    # Load layout
    raw_layout = s.get("keyboard_layout", "")
    try:
        layout = json.loads(raw_layout) if raw_layout else _DEFAULT_LAYOUT
    except Exception:
        layout = _DEFAULT_LAYOUT

    # Load styles for all known buttons
    styles = {bid: s.get(f"btn_style_{bid}", "") for bid in _BUTTON_IDS}

    # Load custom emojis
    emojis = {bid: s.get(f"btn_emoji_{bid}", "") for bid in _BUTTON_IDS}

    return main_menu_kb(
        support_url=s.get("support_url", ""),
        layout=layout,
        styles=styles,
        emojis=emojis,
    )
