from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.models.plan import Plan


def plans_kb(plans: list[Plan], lang: str = "ru") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for plan in plans:
        label = f"{plan.name} — {plan.price} ₽"
        builder.row(InlineKeyboardButton(text=label, callback_data=f"plan:{plan.id}"))
    back_text = "◀️ Назад" if lang == "ru" else "◀️ Back"
    builder.row(InlineKeyboardButton(text=back_text, callback_data="back_main"))
    return builder.as_markup()


def payment_methods_kb(
    plan_id: int,
    stars_amount: int = 0,
    user_balance: float = 0.0,
    plan_price: float = 0.0,
    has_cryptobot: bool = False,
    lang: str = "ru",
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    # ЮКасса
    card_text = "💳 Банковская карта (ЮКасса)" if lang == "ru" else "💳 Bank card (YooKassa)"
    builder.row(InlineKeyboardButton(text=card_text, callback_data=f"pay:yookassa:{plan_id}"))

    # Telegram Stars
    stars_label = (
        f"⭐ Telegram Stars ({stars_amount} ⭐)" if stars_amount
        else "⭐ Telegram Stars"
    )
    builder.row(InlineKeyboardButton(text=stars_label, callback_data=f"pay:stars:{plan_id}"))

    # CryptoBot
    if has_cryptobot:
        crypto_text = "₿ Криптовалюта (CryptoBot)" if lang == "ru" else "₿ Cryptocurrency (CryptoBot)"
        builder.row(InlineKeyboardButton(text=crypto_text, callback_data=f"pay:crypto:{plan_id}"))

    # Баланс
    if user_balance >= plan_price and plan_price > 0:
        bal_text = (
            f"💰 Оплатить с баланса ({user_balance:.0f} ₽)" if lang == "ru"
            else f"💰 Pay from balance ({user_balance:.0f} ₽)"
        )
        builder.row(InlineKeyboardButton(text=bal_text, callback_data=f"pay:balance:{plan_id}"))

    back_text = "◀️ Назад" if lang == "ru" else "◀️ Back"
    builder.row(InlineKeyboardButton(text=back_text, callback_data="buy"))
    return builder.as_markup()
