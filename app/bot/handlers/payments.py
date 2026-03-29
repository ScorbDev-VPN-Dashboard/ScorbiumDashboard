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
from app.services.telegram_stars import TelegramStarsService
from app.utils.log import log

router = Router()


async def _provision_and_notify(user_id: int, payment_id: int, plan_id: int, bot: Bot) -> None:
    """Создаём VPN-подписку и уведомляем пользователя."""
    async with AsyncSessionFactory() as session:
        plan = await PlanService(session).get_by_id(plan_id)
        if not plan:
            return

        key = await VpnKeyService(session).provision(user_id=user_id, plan=plan)

        # Привязываем платёж к ключу
        payment = await PaymentService(session).get_by_id(payment_id)
        if payment and key:
            payment.vpn_key_id = key.id

        await session.commit()

        settings = await BotSettingsService(session).get_all()
        success_msg = settings.get("payment_success_message") or "✅ Оплата прошла успешно!"

        if key and key.access_url:
            text = (
                f"{success_msg}\n\n"
                f"🔑 <b>Ссылка подписки:</b>\n"
                f"<code>{key.access_url}</code>\n\n"
                f"📅 Действует <b>{plan.duration_days} дней</b>\n\n"
                f"💡 Скопируй ссылку и вставь в VPN-клиент"
            )
        else:
            text = f"{success_msg}\n\n⚠️ Не удалось создать ключ. Обратитесь в поддержку."

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
        if not plan or not plan.is_active:
            await callback.answer("Тариф недоступен", show_alert=True)
            return

        from app.services.user import UserService
        user = await UserService(session).get_by_id(callback.from_user.id)
        balance = float(user.balance or 0) if user else 0.0

        if balance < float(plan.price):
            await callback.answer(
                f"❌ Недостаточно средств. Баланс: {balance:.2f} ₽, нужно: {plan.price} ₽",
                show_alert=True,
            )
            return

        updated = await UserService(session).deduct_balance(callback.from_user.id, plan.price)
        if not updated:
            await callback.answer("❌ Ошибка списания баланса", show_alert=True)
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

    await callback.answer("⏳ Оформляем подписку...", show_alert=False)
    await _provision_and_notify(callback.from_user.id, payment_id, plan_id_saved, bot)


# ── YooKassa ──────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("pay:yookassa:"))
async def handle_yookassa_payment(callback: CallbackQuery, bot: Bot) -> None:
    plan_id = int(callback.data.split(":")[2])

    async with AsyncSessionFactory() as session:
        plan = await PlanService(session).get_by_id(plan_id)
        if not plan or not plan.is_active:
            await callback.answer("Тариф недоступен", show_alert=True)
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
            builder.row(InlineKeyboardButton(text="💳 Перейти к оплате", url=confirm_url))
            builder.row(InlineKeyboardButton(
                text="🔄 Проверить оплату",
                callback_data=f"yk:check:{payment_id}:{plan.id}",
            ))
            builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="back_main"))

            try:
                await callback.message.edit_text(
                    f"💳 <b>Оплата через ЮКассу</b>\n\n"
                    f"Тариф: <b>{plan.name}</b> — {plan.price} ₽\n\n"
                    "После оплаты нажмите «Проверить оплату».",
                    reply_markup=builder.as_markup(),
                    parse_mode="HTML",
                )
            except Exception:
                pass
        except Exception as e:
            log.error(f"Yookassa error for user {callback.from_user.id}: {e}")
            async with AsyncSessionFactory() as s2:
                kb = await _get_menu_kb(s2)
            try:
                await callback.message.edit_text("❌ Ошибка при создании платежа. Попробуй позже.", reply_markup=kb)
            except Exception:
                pass

    await callback.answer()


@router.callback_query(F.data.startswith("yk:check:"))
async def handle_yookassa_check(callback: CallbackQuery, bot: Bot) -> None:
    parts = callback.data.split(":")
    payment_id = int(parts[2])
    plan_id = int(parts[3])

    async with AsyncSessionFactory() as session:
        payment = await PaymentService(session).get_by_id(payment_id)
        if not payment or payment.user_id != callback.from_user.id:
            await callback.answer("Платёж не найден", show_alert=True)
            return
        if not payment.external_id:
            await callback.answer("ID платежа не найден. Обратитесь в поддержку.", show_alert=True)
            return

        from app.models.payment import PaymentStatus
        if payment.status == PaymentStatus.SUCCEEDED:
            await callback.answer("✅ Оплата уже подтверждена!", show_alert=True)
            return

        try:
            from app.services.yookassa import YookassaService
            yk_payment = YookassaService().get_payment(payment.external_id)
            if yk_payment.status == "succeeded":
                payment.status = PaymentStatus.SUCCEEDED.value
                await session.commit()
                await callback.answer("✅ Оплата подтверждена!", show_alert=True)
                await _provision_and_notify(callback.from_user.id, payment_id, plan_id, bot)
            elif yk_payment.status == "canceled":
                payment.status = PaymentStatus.FAILED.value
                await session.commit()
                await callback.answer("❌ Платёж отменён.", show_alert=True)
            else:
                await callback.answer("⏳ Оплата ещё не поступила. Попробуйте позже.", show_alert=True)
        except Exception as e:
            log.error(f"YooKassa check error: {e}")
            await callback.answer("❌ Ошибка проверки платежа.", show_alert=True)


# ── Telegram Stars ────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("pay:stars:"))
async def handle_stars_payment(callback: CallbackQuery, bot: Bot) -> None:
    plan_id = int(callback.data.split(":")[2])

    async with AsyncSessionFactory() as session:
        plan = await PlanService(session).get_by_id(plan_id)
        if not plan or not plan.is_active:
            await callback.answer("Тариф недоступен", show_alert=True)
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
        description=f"Подписка на {plan.duration_days} дней",
        payload=f"stars:{payment_id}:{plan_id}",
        stars_amount=stars,
    )

    try:
        if ok:
            await callback.message.edit_text(
                f"⭐ <b>Оплата через Telegram Stars</b>\n\n"
                f"Тариф: <b>{plan.name}</b>\nСтоимость: <b>{stars} ⭐</b>\n\nСчёт отправлен выше 👆",
                reply_markup=back_kb(),
                parse_mode="HTML",
            )
        else:
            async with AsyncSessionFactory() as s2:
                kb = await _get_menu_kb(s2)
            await callback.message.edit_text("❌ Ошибка при создании счёта.", reply_markup=kb)
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


@router.callback_query(F.data.startswith("pay:"))
async def handle_payment_fallback(callback: CallbackQuery) -> None:
    await callback.answer("Неизвестный способ оплаты", show_alert=True)
