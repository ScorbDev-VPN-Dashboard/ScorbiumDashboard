"""
Утилиты для создания кнопок с поддержкой style и icon_custom_emoji_id.
style: 'danger' | 'success' | 'primary' | None (стандартный)
"""
from typing import Optional
from aiogram.types import InlineKeyboardButton


def btn(
    text: str,
    callback_data: str = None,
    url: str = None,
    style: Optional[str] = None,
    emoji_id: Optional[str] = None,
) -> InlineKeyboardButton:

    kwargs = {"text": text}
    if callback_data:
        kwargs["callback_data"] = callback_data
    if url:
        kwargs["url"] = url
    if style in ("danger", "success", "primary"):
        kwargs["style"] = style
    if emoji_id:
        kwargs["icon_custom_emoji_id"] = emoji_id
    return InlineKeyboardButton(**kwargs)
