from aiogram import Router, F
from aiogram.types import CallbackQuery

from app.bot.keyboards.payments import plans_kb, payment_methods_kb
from app.bot.utils.menu import get_main_menu_kb as _get_menu_kb
from app.core.database import AsyncSessionFactory
from app.services.plan import PlanService
from app.services.bot_settings import BotSettingsService
from app.services.user import UserService
from app.services.i18n import t, get_lang

router = Router()


async def _get_user_lang(user_id: int, session) -> str:
    user = await UserService(session).get_by_id(user_id)
    settings = await BotSettingsService(session).get_all()
    user_lang = user.language if user and user.language else None
    return get_lang(settings, user_lang)


@router.callback_query(F.data == "buy")
async def show_plans(callback: CallbackQuery) -> None:
    async with AsyncSessionFactory() as session:
        plans = await PlanService(session).get_all(only_active=True)
        photo = await BotSettingsService(session).get("photo_buy")
        lang = await _get_user_lang(callback.from_user.id, session)

    from app.bot.utils.media import edit_with_photo
    if not plans:
        async with AsyncSessionFactory() as session:
            kb = await _get_menu_kb(session, lang=lang, user_id=callback.from_user.id)
        await edit_with_photo(callback, t("no_plans", lang), reply_markup=kb)
        await callback.answer()
        return

    await edit_with_photo(
        callback,
        t("choose_plan", lang),
        reply_markup=plans_kb(plans, lang=lang),
        photo=photo or None,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("plan:"))
async def select_plan(callback: CallbackQuery) -> None:
    plan_id = int(callback.data.split(":")[1])

    async with AsyncSessionFactory() as session:
        plan = await PlanService(session).get_by_id(plan_id)
        user = await UserService(session).get_by_id(callback.from_user.id)
        settings = await BotSettingsService(session).get_all()
        user_lang = user.language if user and user.language else None
        lang = get_lang(settings, user_lang)
        has_cryptobot = bool(settings.get("cryptobot_token", "").strip())

    if not plan or not plan.is_active:
        await callback.answer(t("no_plans", lang), show_alert=True)
        return

    from app.services.telegram_stars import TelegramStarsService
    stars = TelegramStarsService.rub_to_stars(float(plan.price))
    user_balance = float(user.balance or 0) if user else 0.0

    from app.bot.utils.media import edit_with_photo
    await edit_with_photo(
        callback,
        t("choose_payment", lang, plan_name=plan.name, price=plan.price),
        reply_markup=payment_methods_kb(
            plan_id,
            stars_amount=stars,
            user_balance=user_balance,
            plan_price=float(plan.price),
            has_cryptobot=has_cryptobot,
            lang=lang,
        ),
    )
    await callback.answer()

