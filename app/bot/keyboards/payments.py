from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.models.plan import Plan


def plans_kb(plans: list[Plan]) -> InlineKeyboardMarkup:
    """Pass plans fetched from DB."""
    builder = InlineKeyboardBuilder()
    for plan in plans:
        label = f"{plan.name} — {plan.price} ₽"
        builder.row(InlineKeyboardButton(text=label, callback_data=f"plan:{plan.id}"))
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="back_main"))
    return builder.as_markup()


def payment_methods_kb(plan_id: int, stars_amount: int = 0, user_balance: float = 0.0, plan_price: float = 0.0) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="💳 Банковская карта (ЮКасса)", callback_data=f"pay:yookassa:{plan_id}"))
    stars_label = f"⭐ Telegram Stars ({stars_amount} ⭐)" if stars_amount else "⭐ Telegram Stars"
    builder.row(InlineKeyboardButton(text=stars_label, callback_data=f"pay:stars:{plan_id}"))
    if user_balance >= plan_price and plan_price > 0:
        builder.row(InlineKeyboardButton(
            text=f"💰 Оплатить с баланса ({user_balance:.0f} ₽)",
            callback_data=f"pay:balance:{plan_id}",
        ))
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="buy"))
    return builder.as_markup()
