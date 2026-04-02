"""Утилита для получения главного меню с настройками из БД."""
import json
from aiogram.types import InlineKeyboardMarkup
from app.bot.keyboards.main import main_menu_kb, _DEFAULT_LAYOUT
from app.services.bot_settings import BotSettingsService

_BUTTON_IDS = [
    "my_keys", "buy", "profile", "balance", "promo", "support",
    "connect", "about", "servers", "top_referrers", "status", "language",
]

# Переводы лейблов кнопок по умолчанию
_BTN_LABELS: dict[str, dict[str, str]] = {
    "ru": {
        "my_keys": "🔑 Мои подписки",
        "buy": "💳 Купить",
        "profile": "👤 Профиль",
        "balance": "💰 Баланс",
        "promo": "🎁 Промокод",
        "support": "💬 Поддержка",
        "connect": "📲 Как подключить",
        "about": "ℹ️ О проекте",
        "servers": "🌐 Серверы",
        "top_referrers": "🏆 Топ рефереров",
        "status": "📊 Статус",
        "language": "🌐 Язык",
    },
    "en": {
        "my_keys": "🔑 My subscriptions",
        "buy": "💳 Buy",
        "profile": "👤 Profile",
        "balance": "💰 Balance",
        "promo": "🎁 Promo code",
        "support": "💬 Support",
        "connect": "📲 How to connect",
        "about": "ℹ️ About",
        "servers": "🌐 Servers",
        "top_referrers": "🏆 Top referrers",
        "status": "📊 Status",
        "language": "🌐 Language",
    },
    "fa": {
        "my_keys": "🔑 اشتراک‌های من",
        "buy": "💳 خرید",
        "profile": "👤 پروفایل",
        "balance": "💰 موجودی",
        "promo": "🎁 کد تخفیف",
        "support": "💬 پشتیبانی",
        "connect": "📲 نحوه اتصال",
        "about": "ℹ️ درباره",
        "servers": "🌐 سرورها",
        "top_referrers": "🏆 برترین معرفان",
        "status": "📊 وضعیت",
        "language": "🌐 زبان",
    },
}


def _translate_layout(layout: list, lang: str, settings: dict) -> list:
    """Translate button labels in layout based on user language, with admin overrides."""
    from app.services.i18n import t_custom
    result = []
    for row in layout:
        new_row = []
        for b in row:
            bid = b.get("id", "")
            # Try i18n key btn_{id}, fallback to _BTN_LABELS
            i18n_key = f"btn_{bid}"
            default_label = _BTN_LABELS.get(lang, _BTN_LABELS["ru"]).get(bid, b.get("label", ""))
            # Check admin override in settings
            override = settings.get(f"i18n_{lang}_{i18n_key}", "").strip()
            label = override if override else default_label
            new_row.append({**b, "label": label})
        result.append(new_row)
    return result


async def get_main_menu_kb(session, lang: str = "ru") -> InlineKeyboardMarkup:
    s = await BotSettingsService(session).get_all()

    # Load layout
    raw_layout = s.get("keyboard_layout", "")
    try:
        layout = json.loads(raw_layout) if raw_layout else _DEFAULT_LAYOUT
    except Exception:
        layout = _DEFAULT_LAYOUT

    # Translate labels
    layout = _translate_layout(layout, lang, s)

    # Load styles
    styles = {bid: s.get(f"btn_style_{bid}", "") for bid in _BUTTON_IDS}

    # Load custom emojis
    emojis = {bid: s.get(f"btn_emoji_{bid}", "") for bid in _BUTTON_IDS}

    return main_menu_kb(
        support_url=s.get("support_url", ""),
        layout=layout,
        styles=styles,
        emojis=emojis,
    )
