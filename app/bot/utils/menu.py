"""Утилита для получения главного меню с настройками из БД."""
import json
from aiogram.types import InlineKeyboardMarkup
from app.bot.keyboards.main import main_menu_kb, _DEFAULT_LAYOUT
from app.services.bot_settings import BotSettingsService


async def get_main_menu_kb(session) -> InlineKeyboardMarkup:
    s = await BotSettingsService(session).get_all()

    # Load layout
    raw_layout = s.get("keyboard_layout", "")
    try:
        layout = json.loads(raw_layout) if raw_layout else _DEFAULT_LAYOUT
    except Exception:
        layout = _DEFAULT_LAYOUT

    # Load styles
    styles = {
        "my_keys":       s.get("btn_style_my_keys", "primary"),
        "buy":           s.get("btn_style_buy", "success"),
        "profile":       s.get("btn_style_profile", ""),
        "balance":       s.get("btn_style_balance", ""),
        "promo":         s.get("btn_style_promo", ""),
        "support":       s.get("btn_style_support", ""),
        "connect":       s.get("btn_style_connect", ""),
        "about":         s.get("btn_style_about", ""),
        "servers":       s.get("btn_style_servers", ""),
        "top_referrers": s.get("btn_style_top_referrers", ""),
        "status":        s.get("btn_style_status", ""),
    }

    return main_menu_kb(
        support_url=s.get("support_url", ""),
        layout=layout,
        styles=styles,
    )
