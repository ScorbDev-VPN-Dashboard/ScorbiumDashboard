from aiogram import Router, F
from aiogram.types import CallbackQuery

from app.bot.keyboards.payments import plans_kb, payment_methods_kb
from app.bot.utils.menu import get_main_menu_kb as _get_menu_kb
from app.core.database import AsyncSessionFactory
from app.services.plan import PlanService
from app.services.bot_settings import BotSettingsService

router = Router()


@router.callback_query(F.data == "buy")
async def show_plans(callback: CallbackQuery) -> None:
    async with AsyncSessionFactory() as session:
        plans = await PlanService(session).get_all(only_active=True)
        photo = await BotSettingsService(session).get("photo_buy")

    from app.bot.utils.media import edit_with_photo
    if not plans:
        async with AsyncSessionFactory() as session:
            kb = await _get_menu_kb(session)
        await edit_with_photo(callback, "😔 Нет доступных тарифов. Попробуй позже.", reply_markup=kb)
        await callback.answer()
        return

    await edit_with_photo(
        callback,
        "💳 <b>Выбери план подписки:</b>",
        reply_markup=plans_kb(plans),
        photo=photo or None,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("plan:"))
async def select_plan(callback: CallbackQuery) -> None:
    plan_id = int(callback.data.split(":")[1])

    async with AsyncSessionFactory() as session:
        plan = await PlanService(session).get_by_id(plan_id)
        from app.services.user import UserService
        user = await UserService(session).get_by_id(callback.from_user.id)

    if not plan or not plan.is_active:
        await callback.answer("Тариф недоступен", show_alert=True)
        return

    from app.services.telegram_stars import TelegramStarsService
    stars = TelegramStarsService.rub_to_stars(float(plan.price))
    user_balance = float(user.balance or 0) if user else 0.0

    await callback.message.edit_text(
        f"💳 <b>{plan.name}</b> — {plan.price} ₽\n\nВыбери способ оплаты:",
        reply_markup=payment_methods_kb(plan_id, stars_amount=stars, user_balance=user_balance, plan_price=float(plan.price)),
        parse_mode="HTML",
    )
    await callback.answer()

