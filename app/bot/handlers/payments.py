from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, Message, PreCheckoutQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.keyboards.payments import plans_kb, payment_methods_kb
from app.bot.keyboards.main import back_kb
from app.bot.utils.menu import get_main_menu_kb as _get_menu_kb
from app.core.database import AsyncSessionFactory
from app.models.payment import PaymentProvider
from app.services.payment import PaymentService
from app.services.plan import PlanService
from app.services.vpn_key import VpnKeyService
from app.services.bot_settings import BotSettingsService
from app.services.user import UserService
from app.services.telegram_stars import TelegramStarsService
from app.services.cryptobot import CryptoBotService
from app.services.i18n import t, get_lang
from app.utils.log import log

router = Router()


async def _get_user_lang(user_id: int, session) -> str:
    user = await UserService(session).get_by_id(user_id)
    settings = await BotSettingsService(session).get_all()
    user_lang = user.language if user and user.language else None
    return get_lang(settings, user_lang)


async def _provision_and_notify(user_id: int, payment_id: int, plan_id: int, bot: Bot) -> None:
    """Создаём VPN-подписку и уведомляем пользователя."""
    async with AsyncSessionFactory() as session:
        plan = await PlanService(session).get_by_id(plan_id)
        if not plan:
            return

        key = await VpnKeyService(session).provision(user_id=user_id, plan=plan)

        payment = await PaymentService(session).get_by_id(payment_id)
        if payment and key:
            payment.vpn_key_id = key.id

        await session.commit()

        settings = await BotSettingsService(session).get_all()
        user = await UserService(session).get_by_id(user_id)
        user_lang = user.language if user and user.language else None
        lang = get_lang(settings, user_lang)

        success_msg = settings.get("payment_success_message") or t("payment_success", lang)

        if key and key.access_url:
            text = (
                f"{success_msg}\n\n"
                + t("subscription_url", lang, url=key.access_url, days=plan.duration_days)
            )
        else:
            text = f"{success_msg}\n\n" + t("key_error", lang)

        try:
            await bot.send_message(user_id, text, parse_mode="HTML")
        except Exception as e:
            log.warning(f"Failed to notify user {user_id}: {e}")


# ── Balance ───────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("pay:balance:"))
async def handle_balance_payment(callback: CallbackQuery, bot: Bot) -> None:
    plan_id = int(callback.data.split(":")[2])

    async with AsyncSessionFactory() as session:
        plan = await PlanService(session).get_by_id(plan_id)
        lang = await _get_user_lang(callback.from_user.id, session)
        if not plan or not plan.is_active:
            await callback.answer(t("no_plans", lang), show_alert=True)
            return

        user = await UserService(session).get_by_id(callback.from_user.id)
        balance = float(user.balance or 0) if user else 0.0

        if balance < float(plan.price):
            await callback.answer(
                f"❌ {'Недостаточно средств' if lang=='ru' else 'Insufficient funds'}. {balance:.2f} ₽ / {plan.price} ₽",
                show_alert=True,
            )
            return

        updated = await UserService(session).deduct_balance(callback.from_user.id, plan.price)
        if not updated:
            await callback.answer(t("payment_error", lang), show_alert=True)
            return

        payment = await PaymentService(session).create_pending(
            user_id=callback.from_user.id,
            plan=plan,
            provider=PaymentProvider.BALANCE,
        )
        from app.models.payment import PaymentStatus
        payment.status = PaymentStatus.SUCCEEDED.value
        await session.flush()
        payment_id = payment.id
        plan_id_saved = plan.id
        await session.commit()

    await callback.answer("⏳", show_alert=False)
    await _provision_and_notify(callback.from_user.id, payment_id, plan_id_saved, bot)


# ── YooKassa ──────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("pay:yookassa:"))
async def handle_yookassa_payment(callback: CallbackQuery, bot: Bot) -> None:
    plan_id = int(callback.data.split(":")[2])

    async with AsyncSessionFactory() as session:
        plan = await PlanService(session).get_by_id(plan_id)
        lang = await _get_user_lang(callback.from_user.id, session)
        if not plan or not plan.is_active:
            await callback.answer(t("no_plans", lang), show_alert=True)
            return

        try:
            from app.services.yookassa import YookassaService
            yk = YookassaService()

            payment = await PaymentService(session).create_pending(
                user_id=callback.from_user.id,
                plan=plan,
                provider=PaymentProvider.YOOKASSA,
            )
            await session.flush()
            payment_id = payment.id

            me = await bot.get_me()
            return_url = f"https://t.me/{me.username}"

            yk_payment = yk.create_payment(
                amount=plan.price,
                description=f"VPN — {plan.name}",
                return_url=return_url,
                metadata={"payment_id": str(payment.id), "plan_id": str(plan.id)},
            )
            payment.external_id = yk_payment.id
            await session.commit()

            confirm_url = yk_payment.confirmation.confirmation_url
            builder = InlineKeyboardBuilder()
            builder.row(InlineKeyboardButton(text=t("payment_go", lang), url=confirm_url))
            builder.row(InlineKeyboardButton(
                text=t("payment_check", lang),
                callback_data=f"yk:check:{payment_id}:{plan.id}",
            ))
            builder.row(InlineKeyboardButton(text=t("back", lang), callback_data="back_main"))

            try:
                from app.bot.utils.media import edit_with_photo
                await edit_with_photo(
                    callback,
                    f"💳 <b>{t('pay_card', lang)}</b>\n\n"
                    f"{plan.name} — {plan.price} ₽\n\n"
                    f"{'После оплаты нажмите «Проверить оплату».' if lang=='ru' else 'After payment press Check payment.'}",
                    reply_markup=builder.as_markup(),
                )
            except Exception:
                pass
        except Exception as e:
            log.error(f"Yookassa error for user {callback.from_user.id}: {e}")
            async with AsyncSessionFactory() as s2:
                kb = await _get_menu_kb(s2, lang=lang, user_id=callback.from_user.id)
            try:
                from app.bot.utils.media import edit_with_photo
                await edit_with_photo(callback, t("payment_error", lang), reply_markup=kb)
            except Exception:
                pass

    await callback.answer()


@router.callback_query(F.data.startswith("yk:check:"))
async def handle_yookassa_check(callback: CallbackQuery, bot: Bot) -> None:
    parts = callback.data.split(":")
    payment_id = int(parts[2])
    plan_id = int(parts[3])

    async with AsyncSessionFactory() as session:
        lang = await _get_user_lang(callback.from_user.id, session)
        payment = await PaymentService(session).get_by_id(payment_id)
        if not payment or payment.user_id != callback.from_user.id:
            await callback.answer("❌", show_alert=True)
            return
        if not payment.external_id:
            await callback.answer(t("payment_error", lang), show_alert=True)
            return

        from app.models.payment import PaymentStatus
        if payment.status == PaymentStatus.SUCCEEDED:
            await callback.answer(t("payment_success", lang), show_alert=True)
            return

        try:
            from app.services.yookassa import YookassaService
            yk_payment = YookassaService().get_payment(payment.external_id)
            if yk_payment.status == "succeeded":
                payment.status = PaymentStatus.SUCCEEDED.value
                await session.commit()
                await callback.answer(t("payment_success", lang), show_alert=True)
                await _provision_and_notify(callback.from_user.id, payment_id, plan_id, bot)
            elif yk_payment.status == "canceled":
                payment.status = PaymentStatus.FAILED.value
                await session.commit()
                await callback.answer(t("payment_failed", lang), show_alert=True)
            else:
                await callback.answer(t("payment_pending", lang), show_alert=True)
        except Exception as e:
            log.error(f"YooKassa check error: {e}")
            await callback.answer(t("payment_error", lang), show_alert=True)


# ── СБП (ЮКасса) ─────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("pay:sbp:"))
async def handle_sbp_payment(callback: CallbackQuery, bot: Bot) -> None:
    plan_id = int(callback.data.split(":")[2])

    async with AsyncSessionFactory() as session:
        plan = await PlanService(session).get_by_id(plan_id)
        lang = await _get_user_lang(callback.from_user.id, session)
        if not plan or not plan.is_active:
            await callback.answer(t("no_plans", lang), show_alert=True)
            return

        try:
            from app.services.yookassa import YookassaService
            yk = YookassaService()

            payment = await PaymentService(session).create_pending(
                user_id=callback.from_user.id,
                plan=plan,
                provider=PaymentProvider.YOOKASSA_SBP,
            )
            await session.flush()
            payment_id = payment.id

            me = await bot.get_me()
            return_url = f"https://t.me/{me.username}"

            yk_payment = yk.create_sbp_payment(
                amount=plan.price,
                description=f"VPN — {plan.name}",
                return_url=return_url,
                metadata={"payment_id": str(payment.id), "plan_id": str(plan.id)},
            )
            payment.external_id = yk_payment.id
            await session.commit()

            confirm_url = yk_payment.confirmation.confirmation_url
            sbp_title = {"ru": "🏦 Оплата через СБП", "en": "🏦 SBP Payment", "fa": "🏦 پرداخت SBP"}
            sbp_hint = {
                "ru": "После оплаты нажмите «Проверить оплату».",
                "en": "After payment press Check payment.",
                "fa": "پس از پرداخت، بررسی پرداخت را فشار دهید.",
            }
            builder = InlineKeyboardBuilder()
            builder.row(InlineKeyboardButton(text=t("payment_go", lang), url=confirm_url))
            builder.row(InlineKeyboardButton(
                text=t("payment_check", lang),
                callback_data=f"yk:check:{payment_id}:{plan.id}",
            ))
            builder.row(InlineKeyboardButton(text=t("back", lang), callback_data="back_main"))

            from app.bot.utils.media import edit_with_photo
            await edit_with_photo(
                callback,
                f"🏦 <b>{sbp_title.get(lang, sbp_title['ru'])}</b>\n\n"
                f"{plan.name} — {plan.price} ₽\n\n"
                f"{sbp_hint.get(lang, sbp_hint['ru'])}",
                reply_markup=builder.as_markup(),
            )
        except Exception as e:
            log.error(f"SBP error for user {callback.from_user.id}: {e}")
            async with AsyncSessionFactory() as s2:
                kb = await _get_menu_kb(s2, lang=lang, user_id=callback.from_user.id)
            from app.bot.utils.media import edit_with_photo
            await edit_with_photo(callback, t("payment_error", lang), reply_markup=kb)

    await callback.answer()


# ── Telegram Stars ────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("pay:stars:"))
async def handle_stars_payment(callback: CallbackQuery, bot: Bot) -> None:
    plan_id = int(callback.data.split(":")[2])

    async with AsyncSessionFactory() as session:
        plan = await PlanService(session).get_by_id(plan_id)
        lang = await _get_user_lang(callback.from_user.id, session)
        if not plan or not plan.is_active:
            await callback.answer(t("no_plans", lang), show_alert=True)
            return

        stars = TelegramStarsService.rub_to_stars(float(plan.price))
        payment = await PaymentService(session).create_pending(
            user_id=callback.from_user.id,
            plan=plan,
            provider=PaymentProvider.TELEGRAM_STARS,
        )
        await session.commit()
        payment_id = payment.id

    ok = await TelegramStarsService(bot).send_invoice(
        chat_id=callback.from_user.id,
        title=f"VPN — {plan.name}",
        description=f"{plan.duration_days} {'дней' if lang == 'ru' else 'days'}",
        payload=f"stars:{payment_id}:{plan_id}",
        stars_amount=stars,
    )

    try:
        if ok:
            from app.bot.utils.media import edit_with_photo
            await edit_with_photo(
                callback,
                t("pay_stars", lang, stars=stars),
                reply_markup=back_kb(lang),
            )
        else:
            async with AsyncSessionFactory() as s2:
                kb = await _get_menu_kb(s2, lang=lang, user_id=callback.from_user.id)
            from app.bot.utils.media import edit_with_photo
            await edit_with_photo(callback, t("payment_error", lang), reply_markup=kb)
    except Exception:
        pass

    await callback.answer()


@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery) -> None:
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def successful_payment(message: Message, bot: Bot) -> None:
    payload = message.successful_payment.invoice_payload
    try:
        _, payment_id_str, plan_id_str = payload.split(":")
        payment_id = int(payment_id_str)
        plan_id = int(plan_id_str)
    except (ValueError, AttributeError):
        log.error(f"Invalid Stars payment payload: {payload}")
        return

    charge_id = message.successful_payment.telegram_payment_charge_id
    async with AsyncSessionFactory() as session:
        payment = await PaymentService(session).get_by_id(payment_id)
        if payment:
            from app.models.payment import PaymentStatus
            payment.status = PaymentStatus.SUCCEEDED.value
            payment.external_id = charge_id
            await session.commit()

    await _provision_and_notify(message.from_user.id, payment_id, plan_id, bot)


# ── CryptoBot ─────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("pay:crypto:"))
async def handle_crypto_payment(callback: CallbackQuery, bot: Bot) -> None:
    plan_id = int(callback.data.split(":")[2])

    async with AsyncSessionFactory() as session:
        plan = await PlanService(session).get_by_id(plan_id)
        settings = await BotSettingsService(session).get_all()
        lang = await _get_user_lang(callback.from_user.id, session)

        if not plan or not plan.is_active:
            await callback.answer(t("no_plans", lang), show_alert=True)
            return

        crypto = CryptoBotService.from_settings(settings)
        if not crypto:
            await callback.answer(t("payment_error", lang), show_alert=True)
            return

        try:
            usdt_amount = await crypto.rub_to_usdt(float(plan.price))
            payment = await PaymentService(session).create_pending(
                user_id=callback.from_user.id,
                plan=plan,
                provider=PaymentProvider.CRYPTOBOT,
            )
            await session.flush()
            payment_id = payment.id

            invoice = await crypto.create_invoice(
                amount=usdt_amount,
                currency="USDT",
                description=f"VPN — {plan.name}",
                payload=f"crypto:{payment_id}:{plan_id}",
            )

            if not invoice:
                await session.rollback()
                await callback.answer(t("payment_error", lang), show_alert=True)
                return

            payment.external_id = str(invoice["invoice_id"])
            await session.commit()

            pay_url = invoice.get("pay_url", "")
            builder = InlineKeyboardBuilder()
            builder.row(InlineKeyboardButton(text=t("payment_go", lang), url=pay_url))
            builder.row(InlineKeyboardButton(
                text=t("payment_check", lang),
                callback_data=f"crypto:check:{payment_id}:{plan_id}",
            ))
            builder.row(InlineKeyboardButton(text=t("back", lang), callback_data="back_main"))

            from app.bot.utils.media import edit_with_photo
            await edit_with_photo(
                callback,
                f"₿ <b>{t('pay_crypto', lang)}</b>\n\n"
                f"{plan.name} — {plan.price} ₽ (~{usdt_amount} USDT)\n\n"
                f"{t('payment_check', lang)}.",
                reply_markup=builder.as_markup(),
            )
        except Exception as e:
            log.error(f"CryptoBot error for user {callback.from_user.id}: {e}")
            async with AsyncSessionFactory() as s2:
                kb = await _get_menu_kb(s2, lang=lang, user_id=callback.from_user.id)
            from app.bot.utils.media import edit_with_photo
            await edit_with_photo(callback, t("payment_error", lang), reply_markup=kb)

    await callback.answer()


@router.callback_query(F.data.startswith("crypto:check:"))
async def handle_crypto_check(callback: CallbackQuery, bot: Bot) -> None:
    parts = callback.data.split(":")
    payment_id = int(parts[2])
    plan_id = int(parts[3])

    async with AsyncSessionFactory() as session:
        lang = await _get_user_lang(callback.from_user.id, session)
        payment = await PaymentService(session).get_by_id(payment_id)
        if not payment or payment.user_id != callback.from_user.id:
            await callback.answer("❌", show_alert=True)
            return

        from app.models.payment import PaymentStatus
        if payment.status == PaymentStatus.SUCCEEDED:
            await callback.answer(t("payment_success", lang), show_alert=True)
            return

        settings = await BotSettingsService(session).get_all()
        crypto = CryptoBotService.from_settings(settings)
        if not crypto or not payment.external_id:
            await callback.answer(t("payment_error", lang), show_alert=True)
            return

        try:
            invoice = await crypto.get_invoice(int(payment.external_id))
            if invoice and invoice.get("status") == "paid":
                payment.status = PaymentStatus.SUCCEEDED.value
                await session.commit()
                await callback.answer(t("payment_success", lang), show_alert=True)
                await _provision_and_notify(callback.from_user.id, payment_id, plan_id, bot)
            else:
                await callback.answer(t("payment_pending", lang), show_alert=True)
        except Exception as e:
            log.error(f"CryptoBot check error: {e}")
            await callback.answer(t("payment_error", lang), show_alert=True)


@router.callback_query(F.data.startswith("pay:"))
async def handle_payment_fallback(callback: CallbackQuery) -> None:
    await callback.answer("❌", show_alert=True)
