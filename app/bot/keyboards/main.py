"""
Главное меню бота — строится динамически из keyboard_layout в bot_settings.
"""
import json
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from app.bot.keyboards.builder import btn

# Дефолтная раскладка если в БД ничего нет
_DEFAULT_LAYOUT = [
    [{"id": "my_keys",  "label": "🔑 Мои подписки",  "callback": "my_keys"}],
    [{"id": "buy",      "label": "💳 Купить",         "callback": "buy"}],
    [{"id": "balance",  "label": "💰 Баланс",         "callback": "balance"},
     {"id": "promo",    "label": "🎁 Промокод",       "callback": "enter_promo"}],
    [{"id": "connect",  "label": "📲 Как подключить", "callback": "connect:menu"},
     {"id": "about",    "label": "ℹ️ О проекте",      "callback": "about"}],
    [{"id": "profile",  "label": "👤 Профиль",        "callback": "profile"},
     {"id": "servers",  "label": "🌐 Серверы",        "callback": "servers"}],
    [{"id": "top_referrers", "label": "🏆 Топ рефереров", "callback": "top_referrers"}],
    [{"id": "support",  "label": "💬 Поддержка",      "callback": "support"}],
]


def main_menu_kb(
    support_url: str = "",
    layout: list = None,
    styles: dict = None,
    emojis: dict = None,
    **kwargs,
) -> InlineKeyboardMarkup:
    """
    Строит клавиатуру из layout.
    layout = [[{id, label, callback}, ...], ...]
    styles = {btn_id: "success"|"primary"|"danger"|""}  — только для панели
    emojis = {btn_id: "emoji_id"}
    """
    if layout is None:
        layout = _DEFAULT_LAYOUT
    if styles is None:
        styles = {}
    if emojis is None:
        emojis = {}

    builder = InlineKeyboardBuilder()

    for row in layout:
        if not row:
            continue
        row_btns = []
        for b in row:
            bid = b.get("id", "")
            label = b.get("label", "")
            callback = b.get("callback", bid)
            emoji_id = emojis.get(bid) or None

            # Support button — may use URL
            if bid == "support" and support_url:
                row_btns.append(btn(label, url=support_url, emoji_id=emoji_id))
            else:
                row_btns.append(btn(label, callback_data=callback, emoji_id=emoji_id))

        if len(row_btns) == 1:
            builder.row(row_btns[0])
        else:
            builder.row(*row_btns)

    return builder.as_markup()


def back_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="◀️ Главное меню", callback_data="back_main"))
    return builder.as_markup()
