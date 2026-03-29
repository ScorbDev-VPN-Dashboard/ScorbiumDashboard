"""
Утилиты для отправки сообщений.

Важно: Telegram поддерживает style (цвет кнопок) ТОЛЬКО при send_message,
но НЕ при edit_message. Поэтому edit_with_photo всегда удаляет старое
сообщение и отправляет новое — это единственный способ сохранить стили.
"""
from typing import Optional
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup
from aiogram.exceptions import TelegramBadRequest


async def answer_with_photo(
    message: Message,
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    photo: Optional[str] = None,
    parse_mode: str = "HTML",
) -> Message:
    """Отправляет новое сообщение — с фото если есть file_id, иначе текст."""
    if photo:
        return await message.answer_photo(
            photo=photo,
            caption=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )
    return await message.answer(
        text=text,
        reply_markup=reply_markup,
        parse_mode=parse_mode,
    )


async def edit_with_photo(
    callback: CallbackQuery,
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    photo: Optional[str] = None,
    parse_mode: str = "HTML",
) -> None:
    """
    Отправляет новое сообщение вместо редактирования.
    Это необходимо чтобы style (цвет кнопок) применялся корректно —
    Telegram игнорирует style при edit_message.
    """
    msg = callback.message

    # Удаляем старое сообщение
    try:
        await msg.delete()
    except Exception:
        pass

    # Отправляем новое
    try:
        if photo:
            await msg.answer_photo(
                photo=photo,
                caption=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
            )
        else:
            await msg.answer(
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
            )
    except Exception:
        # Fallback: попробуем edit если send не сработал
        try:
            await msg.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
        except Exception:
            pass
