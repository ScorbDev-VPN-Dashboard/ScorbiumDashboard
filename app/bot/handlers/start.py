import secrets
from aiogram import Router, F
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.keyboards.main import main_menu_kb, back_kb
from app.bot.utils.menu import get_main_menu_kb as _get_menu_kb
from app.core.database import AsyncSessionFactory
from app.schemas.user import UserCreate
from app.services.user import UserService
from app.services.referral import ReferralService
from app.services.promo import PromoService
from app.services.bot_settings import BotSettingsService
from app.services.support import SupportService
from app.services.telegram_notify import TelegramNotifyService
from app.core.config import config

router = Router()


class PromoState(StatesGroup):
    waiting_code = State()


class SupportState(StatesGroup):
    waiting_subject = State()
    waiting_message = State()
    replying_ticket = State() 


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    args = message.text.split(maxsplit=1)
    ref_code = args[1].strip() if len(args) > 1 else None

    async with AsyncSessionFactory() as session:
        svc = UserService(session)
        user, created = await svc.get_or_create(
            UserCreate(
                id=message.from_user.id,
                username=message.from_user.username,
                full_name=message.from_user.full_name,
            )
        )

        if not user.referral_code:
            user.referral_code = secrets.token_urlsafe(6).upper()

        if created and ref_code and ref_code != user.referral_code:
            referrer = await svc.get_by_referral_code(ref_code)
            if referrer and referrer.id != user.id:
                ref_svc = ReferralService(session)
                settings_svc = BotSettingsService(session)
                bonus_type = await settings_svc.get("referral_bonus_type") or "days"
                bonus_value_str = await settings_svc.get("referral_bonus_value") or "3"
                from decimal import Decimal
                bonus_value = Decimal(bonus_value_str)
                bonus_days = int(bonus_value) if bonus_type == "days" else 0
                await ref_svc.create(
                    referrer_id=referrer.id,
                    referred_id=user.id,
                    bonus_days=bonus_days,
                    bonus_type=bonus_type,
                    bonus_value=bonus_value,
                )

        await session.commit()

        settings_svc = BotSettingsService(session)
        welcome_tpl = await settings_svc.get("welcome_message")
        kb = await _get_menu_kb(session)

    welcome = (welcome_tpl or "👋 Привет, {name}!\n\nЭто VPN-бот. Выбери действие:").format(
        name=message.from_user.first_name
    )
    if not created:
        welcome = welcome.replace("Привет", "С возвращением").replace("Добро пожаловать", "С возвращением")

    from app.bot.utils.media import answer_with_photo
    async with AsyncSessionFactory() as session:
        photo = await BotSettingsService(session).get("photo_welcome")
    await answer_with_photo(message, welcome, reply_markup=kb, photo=photo or None)


@router.callback_query(F.data == "back_main")
async def back_to_main(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    async with AsyncSessionFactory() as session:
        kb = await _get_menu_kb(session)
        photo = await BotSettingsService(session).get("photo_welcome")
    from app.bot.utils.media import edit_with_photo
    await edit_with_photo(callback, "Главное меню:", reply_markup=kb, photo=photo or None)
    await callback.answer()


@router.callback_query(F.data == "balance")
async def show_balance(callback: CallbackQuery) -> None:
    async with AsyncSessionFactory() as session:
        user = await UserService(session).get_by_id(callback.from_user.id)
        ref_count = await ReferralService(session).count_referrals(callback.from_user.id)
        settings = await BotSettingsService(session).get_all()
        balance = float(user.balance or 0) if user else 0.0
        referral_code = user.referral_code if user else None
        photo = settings.get("photo_balance") or None

    bot_username = await _get_bot_username()
    ref_link = f"https://t.me/{bot_username}?start={referral_code}" if referral_code and bot_username else "—"

    bonus_type = settings.get("referral_bonus_type", "days")
    bonus_value = settings.get("referral_bonus_value", "3")
    bonus_labels = {"days": f"+{bonus_value} дней", "balance": f"+{bonus_value} ₽", "percent": f"{bonus_value}% скидка"}
    bonus_text = bonus_labels.get(bonus_type, f"+{bonus_value}")

    text = (
        f"💰 <b>Ваш баланс:</b> <b>{balance:.2f} ₽</b>\n\n"
        f"👥 <b>Рефералов:</b> {ref_count}\n"
        f"🎁 <b>Бонус за реферала:</b> {bonus_text}\n\n"
        f"🔗 <b>Ваша реферальная ссылка:</b>\n<code>{ref_link}</code>"
    )
    from app.bot.utils.media import edit_with_photo
    await edit_with_photo(callback, text, reply_markup=back_kb(), photo=photo)
    await callback.answer()


@router.callback_query(F.data == "enter_promo")
async def ask_promo(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(PromoState.waiting_code)
    await callback.message.edit_text("🎁 Введите промокод:", reply_markup=back_kb())
    await callback.answer()


@router.message(PromoState.waiting_code)
async def process_promo(message: Message, state: FSMContext) -> None:
    code = message.text.strip().upper()
    async with AsyncSessionFactory() as session:
        promo = await PromoService(session).apply(code)
        if promo:
            pt = str(promo.promo_type)
            if pt == "balance":
                await UserService(session).add_balance(message.from_user.id, promo.value)
                result_text = f"✅ Промокод применён!\n\n💰 На баланс зачислено <b>{promo.value} ₽</b>"
            elif pt == "days":
                result_text = f"✅ Промокод применён!\n\n📅 Добавлено <b>{int(promo.value)} дней</b> к подписке"
            else:
                result_text = f"✅ Промокод применён!\n\n🏷 Скидка <b>{promo.value}%</b> на следующую покупку"
            await session.commit()
        else:
            result_text = "❌ Промокод недействителен или уже использован"

        kb = await _get_menu_kb(session)

    await state.clear()
    await message.answer(result_text, reply_markup=kb, parse_mode="HTML")


# ── Support FSM ───────────────────────────────────────────────────────────────

@router.callback_query(F.data == "support")
async def support_start(callback: CallbackQuery, state: FSMContext) -> None:
    """Show user's tickets + option to create new."""
    async with AsyncSessionFactory() as session:
        tickets = await SupportService(session).get_for_user(callback.from_user.id)
        # Extract data while session is open
        ticket_rows = [
            {
                "id": t.id,
                "subject": t.subject,
                "status": t.status.value if hasattr(t.status, "value") else str(t.status),
            }
            for t in tickets
        ]

    builder = InlineKeyboardBuilder()
    if ticket_rows:
        for t in ticket_rows[:5]:
            st_icon = {"open": "🔵", "in_progress": "🟡", "closed": "⚫"}.get(t["status"], "❓")
            label = f"{st_icon} #{t['id']} — {t['subject'][:28]}"
            builder.row(InlineKeyboardButton(text=label, callback_data=f"support:ticket:{t['id']}"))

    builder.row(InlineKeyboardButton(text="➕ Новый тикет", callback_data="support:new"))
    builder.row(InlineKeyboardButton(text="◀️ Главное меню", callback_data="back_main"))

    text = "💬 <b>Поддержка</b>\n\n"
    if ticket_rows:
        text += f"Ваши обращения ({len(ticket_rows)}):\n\nВыберите тикет для продолжения или создайте новый."
    else:
        text += "У вас нет обращений. Создайте новый тикет."

    async with AsyncSessionFactory() as session:
        photo = await BotSettingsService(session).get("photo_support")
    from app.bot.utils.media import edit_with_photo
    await edit_with_photo(callback, text, reply_markup=builder.as_markup(), photo=photo or None)
    await callback.answer()


@router.callback_query(F.data == "support:new")
async def support_new(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SupportState.waiting_subject)
    await callback.message.edit_text(
        "💬 <b>Новое обращение</b>\n\nВведите тему обращения (кратко):",
        reply_markup=back_kb(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("support:ticket:"))
async def support_open_ticket(callback: CallbackQuery, state: FSMContext) -> None:
    ticket_id = int(callback.data.split(":")[2])
    async with AsyncSessionFactory() as session:
        ticket = await SupportService(session).get_by_id(ticket_id)
        if not ticket or ticket.user_id != callback.from_user.id:
            await callback.answer("Тикет не найден", show_alert=True)
            return

        # Extract all data while session is open
        subject = ticket.subject
        st_val = ticket.status.value if hasattr(ticket.status, "value") else str(ticket.status)
        msgs = [
            {"is_admin": bool(m.is_admin), "text": m.text}
            for m in (ticket.messages[-5:] if ticket.messages else [])
        ]

    text = f"💬 <b>Тикет #{ticket_id}</b>\n📌 {subject}\n\n"
    for m in msgs:
        who = "🛡 Поддержка" if m["is_admin"] else "👤 Вы"
        text += f"<b>{who}:</b> {m['text'][:200]}\n\n"

    status_label = {"open": "🔵 Открыт", "in_progress": "🟡 В работе", "closed": "⚫ Закрыт"}.get(st_val, st_val)
    text += f"Статус: {status_label}"

    builder = InlineKeyboardBuilder()
    if st_val != "closed":
        builder.row(InlineKeyboardButton(text="✏️ Написать ответ", callback_data=f"support:reply:{ticket_id}"))
        builder.row(InlineKeyboardButton(text="🔒 Закрыть тикет", callback_data=f"support:close:{ticket_id}"))
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="support"))

    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("support:reply:"))
async def support_reply_start(callback: CallbackQuery, state: FSMContext) -> None:
    ticket_id = int(callback.data.split(":")[2])
    await state.set_state(SupportState.replying_ticket)
    await state.update_data(ticket_id=ticket_id)
    await callback.message.edit_text(
        f"✏️ Введите ваш ответ по тикету #{ticket_id}:",
        reply_markup=back_kb(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("support:close:"))
async def support_close_ticket(callback: CallbackQuery) -> None:
    ticket_id = int(callback.data.split(":")[2])
    async with AsyncSessionFactory() as session:
        ticket = await SupportService(session).get_by_id(ticket_id)
        if not ticket or ticket.user_id != callback.from_user.id:
            await callback.answer("Тикет не найден", show_alert=True)
            return
        from app.models.support import TicketStatus
        await SupportService(session).set_status(ticket_id, TicketStatus.CLOSED)
        await session.commit()
        kb = await _get_menu_kb(session)

    await callback.message.edit_text(
        f"✅ <b>Тикет #{ticket_id} закрыт.</b>\n\nСпасибо за обращение!",
        reply_markup=kb,
        parse_mode="HTML",
    )
    await callback.answer("Тикет закрыт")


@router.message(SupportState.replying_ticket)
async def support_reply_message(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    ticket_id = data.get("ticket_id")
    text = message.text.strip()

    async with AsyncSessionFactory() as session:
        msg = await SupportService(session).add_message(
            ticket_id=ticket_id,
            sender_id=message.from_user.id,
            text=text,
            is_admin=False,
        )
        await session.commit()
        kb = await _get_menu_kb(session)

    await state.clear()

    if msg:
        await message.answer(
            f"✅ Ответ по тикету #{ticket_id} отправлен!\n\nМы ответим вам в ближайшее время.",
            reply_markup=kb,
            parse_mode="HTML",
        )
        # Notify admins
        notify = TelegramNotifyService()
        uname = f"@{message.from_user.username}" if message.from_user.username else f"id:{message.from_user.id}"
        for admin_id in config.telegram.telegram_admin_ids:
            await notify.send_message(
                admin_id,
                f"💬 <b>Ответ в тикете #{ticket_id}</b>\n\n👤 {uname}:\n{text[:300]}",
            )
    else:
        await message.answer("❌ Тикет не найден.", reply_markup=kb)


@router.message(SupportState.waiting_subject)
async def support_subject(message: Message, state: FSMContext) -> None:
    subject = message.text.strip()
    if len(subject) < 3:
        await message.answer("Тема слишком короткая. Введите ещё раз:")
        return
    await state.update_data(subject=subject)
    await state.set_state(SupportState.waiting_message)
    await message.answer(
        f"📝 Тема: <b>{subject}</b>\n\nТеперь опишите вашу проблему подробнее:",
        reply_markup=back_kb(),
        parse_mode="HTML",
    )


@router.message(SupportState.waiting_message)
async def support_message(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    subject = data.get("subject", "Без темы")
    text = message.text.strip()

    async with AsyncSessionFactory() as session:
        ticket = await SupportService(session).create_ticket(
            user_id=message.from_user.id,
            subject=subject,
            first_message=text,
        )
        await session.commit()
        ticket_id = ticket.id
        kb = await _get_menu_kb(session)

    await state.clear()
    await message.answer(
        f"✅ <b>Тикет #{ticket_id} создан!</b>\n\n"
        f"Тема: <b>{subject}</b>\n\n"
        "Мы ответим вам в ближайшее время.",
        reply_markup=kb,
        parse_mode="HTML",
    )

    # Уведомляем всех администраторов
    notify = TelegramNotifyService()
    uname = f"@{message.from_user.username}" if message.from_user.username else f"id:{message.from_user.id}"
    admin_text = (
        f"🆕 <b>Новый тикет #{ticket_id}</b>\n\n"
        f"👤 Пользователь: {uname}\n"
        f"📌 Тема: <b>{subject}</b>\n\n"
        f"💬 {text[:300]}"
    )
    for admin_id in config.telegram.telegram_admin_ids:
        await notify.send_message(admin_id, admin_text)


async def _get_bot_username() -> str:
    try:
        from app.core.config import config
        from aiogram import Bot
        from aiogram.client.default import DefaultBotProperties
        from aiogram.enums import ParseMode
        bot = Bot(
            token=config.telegram.telegram_bot_token.get_secret_value(),
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        me = await bot.get_me()
        await bot.session.close()
        return me.username or ""
    except Exception:
        return ""
