from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from app.core.config import config
from app.core.database import AsyncSessionFactory
from app.services.user import UserService
from app.services.payment import PaymentService
from app.services.vpn_key import VpnKeyService
from app.services.support import SupportService
from app.services.promo import PromoService
from app.services.referral import ReferralService
from app.services.broadcast import BroadcastService
from app.services.plan import PlanService
from app.models.payment import PaymentStatus, PaymentType
from app.utils.log import log
from app.utils.html_utils import sanitize_search_query

router = Router()


class PromoCreateState(StatesGroup):
    waiting_code = State()
    waiting_type = State()
    waiting_value = State()
    waiting_max_uses = State()


class BalanceState(StatesGroup):
    waiting_amount_add = State()
    waiting_amount_deduct = State()


class BroadcastState(StatesGroup):
    waiting_text = State()
    waiting_target = State()


class SearchState(StatesGroup):
    waiting_query = State()


class GiftKeyState(StatesGroup):
    waiting_user_id = State()
    waiting_plan = State()


def _is_admin(user_id: int) -> bool:
    return user_id in config.telegram.telegram_admin_ids


def admin_kb(panel_url: str = "") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="👥 Пользователи", callback_data="adm:users"),
        InlineKeyboardButton(text="📊 Статистика", callback_data="adm:stats"),
    )
    builder.row(
        InlineKeyboardButton(text="💬 Тикеты", callback_data="adm:tickets"),
        InlineKeyboardButton(text="💳 Платежи", callback_data="adm:payments"),
    )
    builder.row(
        InlineKeyboardButton(text="🎁 Промокоды", callback_data="adm:promos"),
        InlineKeyboardButton(text="👥 Рефералы", callback_data="adm:referrals"),
    )
    builder.row(
        InlineKeyboardButton(text="🔑 VPN ключи", callback_data="adm:keys"),
        InlineKeyboardButton(text="📢 Рассылка", callback_data="adm:broadcast"),
    )
    builder.row(
        InlineKeyboardButton(text="🌐 Группы VPN", callback_data="adm:groups"),
        InlineKeyboardButton(text="🔍 Поиск юзера", callback_data="adm:search"),
    )
    if panel_url:
        from aiogram.types import WebAppInfo

        builder.row(
            InlineKeyboardButton(
                text="🖥 Открыть панель", web_app=WebAppInfo(url=panel_url)
            )
        )
    return builder.as_markup()


def _back_admin_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="adm:back"))
    return builder.as_markup()


async def _admin_main_text() -> tuple[str, InlineKeyboardMarkup]:
    from datetime import datetime, timezone, timedelta
    from sqlalchemy import select, func
    from app.models.payment import Payment
    from app.models.user import User

    async with AsyncSessionFactory() as session:
        total_users = await UserService(session).count_all()
        active_subs = await VpnKeyService(session).count_active()
        open_tickets = await SupportService(session).count_open()
        revenue = await PaymentService(session).total_revenue()
        pending = await PaymentService(session).count_by_status(PaymentStatus.PENDING)

        today = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        new_today_r = await session.execute(
            select(func.count()).select_from(User).where(User.created_at >= today)
        )
        new_today = new_today_r.scalar_one()

        from sqlalchemy import cast, Numeric

        rev_today_r = await session.execute(
            select(
                func.coalesce(func.sum(cast(Payment.amount, Numeric)), 0).label("total")
            ).where(
                Payment.status == PaymentStatus.SUCCEEDED.value,
                Payment.payment_type == PaymentType.SUBSCRIPTION.value,
                Payment.created_at >= today,
            )
        )
        rev_today_val = rev_today_r.scalar_one()
        rev_today = float(rev_today_val) if rev_today_val else 0.0

        from app.services.bot_settings import BotSettingsService

        panel_url = (await BotSettingsService(session).get("panel_url") or "").rstrip(
            "/"
        )

    miniapp_url = ""
    if panel_url:
        import secrets as _sec, time as _t
        from app.api.panel.views import _miniapp_tokens

        token = _sec.token_urlsafe(32)
        _miniapp_tokens[token] = _t.time() + 300
        miniapp_url = f"{panel_url}/panel/miniapp-login?token={token}"

    text = (
        f"👑 <b>Админ-панель</b>\n\n"
        f"👥 Пользователей: <b>{total_users}</b> (+{new_today} сегодня)\n"
        f"✅ Активных подписок: <b>{active_subs}</b>\n"
        f"💬 Открытых тикетов: <b>{open_tickets}</b>\n"
        f"⏳ Ожидают оплаты: <b>{pending}</b>\n"
        f"💰 Выручка всего: <b>{revenue} ₽</b>\n"
        f"📈 Выручка сегодня: <b>{rev_today:.2f} ₽</b>"
    )
    return text, admin_kb(panel_url=miniapp_url)


async def _show_user_detail(callback: CallbackQuery, user_id: int) -> None:
    async with AsyncSessionFactory() as session:
        user = await UserService(session).get_by_id(user_id)
        if not user:
            try:
                await callback.message.edit_text(
                    "Пользователь не найден", reply_markup=_back_admin_kb()
                )
            except Exception:
                pass
            return
        keys = await VpnKeyService(session).get_all_for_user(user_id)
        payments = await PaymentService(session).get_all(user_id=user_id, limit=3)
        is_banned = user.is_banned
        full_name = user.full_name
        username = user.username
        balance = float(user.balance or 0)
        reg_date = user.created_at.strftime("%d.%m.%Y") if user.created_at else "—"
        active_keys = [
            k
            for k in keys
            if str(k.status.value if hasattr(k.status, "value") else k.status)
            == "active"
        ]
        active_key = active_keys[0] if active_keys else None
        active_exp = (
            active_key.expires_at.strftime("%d.%m.%Y")
            if active_key and active_key.expires_at
            else None
        )
        total_spent = sum(
            float(p.amount)
            for p in payments
            if str(p.status.value if hasattr(p.status, "value") else p.status)
            == "succeeded"
        )

    uname = f"@{username}" if username else f"<code>{user_id}</code>"
    safe_name = (
        full_name.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        if full_name
        else "—"
    )
    text = (
        f"👤 <b>{safe_name}</b> ({uname})\n\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"📅 Регистрация: {reg_date}\n"
        f"Статус: {'🚫 Забанен' if bool(is_banned) else '✅ Активен'}\n"
        f"💰 Баланс: <b>{balance:.2f} ₽</b>\n"
        f"💳 Потрачено: <b>{total_spent:.2f} ₽</b>\n"
        f"🔑 Подписок: {len(keys)} (активных: {len(active_keys)})\n"
    )
    if active_exp:
        text += f"📅 Активна до: {active_exp}\n"

    builder = InlineKeyboardBuilder()
    if bool(is_banned):
        builder.row(
            InlineKeyboardButton(
                text="✅ Разбанить", callback_data=f"adm:unban:{user_id}"
            )
        )
    else:
        builder.row(
            InlineKeyboardButton(text="🚫 Забанить", callback_data=f"adm:ban:{user_id}")
        )
    builder.row(
        InlineKeyboardButton(
            text="💰 Пополнить", callback_data=f"adm:addbal:{user_id}"
        ),
        InlineKeyboardButton(text="💸 Снять", callback_data=f"adm:deductbal:{user_id}"),
    )
    builder.row(
        InlineKeyboardButton(text="🔑 Ключи", callback_data=f"adm:userkeys:{user_id}"),
        InlineKeyboardButton(
            text="🎁 Подарить ключ", callback_data=f"adm:giftkey:{user_id}"
        ),
    )
    builder.row(
        InlineKeyboardButton(text="✉️ Написать", callback_data=f"adm:msg:{user_id}")
    )
    builder.row(InlineKeyboardButton(text="◀️ К списку", callback_data="adm:users"))
    try:
        await callback.message.edit_text(
            text, reply_markup=builder.as_markup(), parse_mode="HTML"
        )
    except Exception:
        pass


async def _show_user_keys(callback: CallbackQuery, user_id: int) -> None:
    async with AsyncSessionFactory() as session:
        keys = await VpnKeyService(session).get_all_for_user(user_id)

    builder = InlineKeyboardBuilder()
    if not keys:
        builder.row(
            InlineKeyboardButton(text="◀️ Назад", callback_data=f"adm:user:{user_id}")
        )
        try:
            await callback.message.edit_text(
                f"🔑 У пользователя {user_id} нет ключей",
                reply_markup=builder.as_markup(),
            )
        except Exception:
            pass
        return

    lines = [f"🔑 <b>Ключи пользователя {user_id}</b>\n"]
    for k in keys:
        st = str(k.status.value if hasattr(k.status, "value") else k.status)
        icon = {"active": "✅", "revoked": "🚫", "expired": "⏰"}.get(st, "❓")
        exp = k.expires_at.strftime("%d.%m.%Y") if k.expires_at else "—"
        lines.append(f"{icon} #{k.id} — {(k.name or '')[:25]} до {exp}")
        if st == "active":
            builder.row(
                InlineKeyboardButton(
                    text=f"🚫 Отозвать #{k.id}",
                    callback_data=f"adm:revokekey:{k.id}:{user_id}",
                )
            )
    builder.row(
        InlineKeyboardButton(text="◀️ Назад", callback_data=f"adm:user:{user_id}")
    )
    try:
        await callback.message.edit_text(
            "\n".join(lines), reply_markup=builder.as_markup(), parse_mode="HTML"
        )
    except Exception:
        pass


async def _show_groups(callback: CallbackQuery, saved_ids: list[int]) -> None:
    from app.services.pasarguard.pasarguard import PasarguardService

    try:
        groups = await PasarguardService().get_groups()
    except Exception:
        groups = []

    if not groups:
        try:
            await callback.message.edit_text(
                "🌐 <b>Группы VPN</b>\n\n❌ Не удалось загрузить группы из Marzban.",
                reply_markup=_back_admin_kb(),
                parse_mode="HTML",
            )
        except Exception:
            pass
        return

    lines = [
        "🌐 <b>Группы VPN (Marzban)</b>\n",
        "Нажми на группу чтобы включить/выключить:\n",
    ]
    builder = InlineKeyboardBuilder()
    for g in groups:
        gid = g["id"]
        icon = "✅" if gid in saved_ids else "⬜"
        disabled = " 🔴" if g.get("is_disabled") else ""
        builder.row(
            InlineKeyboardButton(
                text=f"{icon} {g['name']}{disabled} ({g.get('total_users', 0)} юз.)",
                callback_data=f"adm:group:toggle:{gid}",
            )
        )
        inbounds = ", ".join(g.get("inbound_tags", []))
        lines.append(f"{icon} <b>{g['name']}</b> — {inbounds}")

    lines.append(
        f"\n💾 Активные: <code>{saved_ids}</code>"
        if saved_ids
        else "\n⚠️ Группы не выбраны"
    )
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="adm:back"))
    try:
        await callback.message.edit_text(
            "\n".join(lines), reply_markup=builder.as_markup(), parse_mode="HTML"
        )
    except Exception:
        pass


# ── Main handlers ─────────────────────────────────────────────────────────────


@router.message(Command("admin"))
async def admin_panel(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        await message.answer("⛔ Нет доступа.")
        return
    text, kb = await _admin_main_text()
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "adm:back")
async def admin_back(callback: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(callback.from_user.id):
        return
    await state.clear()
    text, kb = await _admin_main_text()
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "adm:stats")
async def admin_stats(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return

    from datetime import datetime, timezone, timedelta
    from sqlalchemy import select, func
    from app.models.payment import Payment
    from app.models.user import User
    from app.models.vpn_key import VpnKey, VpnKeyStatus

    async with AsyncSessionFactory() as session:
        total_users = await UserService(session).count_all()
        active_subs = await VpnKeyService(session).count_active()
        open_tickets = await SupportService(session).count_open()
        revenue = await PaymentService(session).total_revenue()
        pending = await PaymentService(session).count_by_status(PaymentStatus.PENDING)

        today = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        week_ago = today - timedelta(days=7)

        new_today_r = await session.execute(
            select(func.count()).select_from(User).where(User.created_at >= today)
        )
        new_today = new_today_r.scalar_one()

        new_week_r = await session.execute(
            select(func.count()).select_from(User).where(User.created_at >= week_ago)
        )
        new_week = new_week_r.scalar_one()

        from sqlalchemy import cast, Numeric

        rev_today_r = await session.execute(
            select(
                func.coalesce(func.sum(cast(Payment.amount, Numeric)), 0).label("total")
            ).where(
                Payment.status == PaymentStatus.SUCCEEDED.value,
                Payment.payment_type == PaymentType.SUBSCRIPTION.value,
                Payment.created_at >= today,
            )
        )
        rev_today_val = rev_today_r.scalar_one()
        rev_today = float(rev_today_val) if rev_today_val else 0.0

        rev_week_r = await session.execute(
            select(
                func.coalesce(func.sum(cast(Payment.amount, Numeric)), 0).label("total")
            ).where(
                Payment.status == PaymentStatus.SUCCEEDED.value,
                Payment.payment_type == PaymentType.SUBSCRIPTION.value,
                Payment.created_at >= week_ago,
            )
        )
        rev_week_val = rev_week_r.scalar_one()
        rev_week = float(rev_week_val) if rev_week_val else 0.0

        expired_r = await session.execute(
            select(func.count())
            .select_from(VpnKey)
            .where(VpnKey.status == VpnKeyStatus.EXPIRED.value)
        )
        expired_count = expired_r.scalar_one()

    text = (
        f"   [📊] <b>Статистика</b>\n\n"
        f"[👤] <b>├Пользователи:</b>\n"
        f"  ⎡ Всего: <b>{total_users}</b>\n"
        f"  ├ Новых сегодня: <b>{new_today}</b>\n"
        f"  ⎣ Новых за неделю: <b>{new_week}</b>\n\n"
        f"[🔑] <b>Подписки:</b>\n"
        f"  ⎡ Активных: <b>{active_subs}</b>\n"
        f"  ⎣ Истёкших: <b>{expired_count}</b>\n\n"
        f"[🏦] <b>Финансы:</b>\n"
        f"  ⎡ Выручка всего: <b>{revenue} ₽</b>\n"
        f"  ├ Выручка сегодня: <b>{rev_today:.2f} ₽</b>\n"
        f"  ⎣ Выручка за неделю: <b>{rev_week:.2f} ₽</b>\n\n"
        f"[ℹ️] <b>Прочее:</b>\n"
        f"  ⎡ Открытых тикетов: <b>{open_tickets}</b>\n"
        f"  ⎣ Ожидают оплаты: <b>{pending}</b>"
    )
    await callback.message.edit_text(
        text, reply_markup=_back_admin_kb(), parse_mode="HTML"
    )
    await callback.answer()


# ── Users ─────────────────────────────────────────────────────────────────────


@router.callback_query(F.data == "adm:users")
async def admin_users(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    await callback.answer()
    await _show_users_page(callback, page=0)


@router.callback_query(F.data.startswith("adm:users:page:"))
async def admin_users_page(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        return
    await callback.answer()
    page = int(callback.data.split(":")[3])
    await _show_users_page(callback, page=page)


async def _show_users_page(callback: CallbackQuery, page: int = 0) -> None:
    PAGE_SIZE = 8
    offset = page * PAGE_SIZE

    async with AsyncSessionFactory() as session:
        users = await UserService(session).get_all(limit=PAGE_SIZE, offset=offset)
        total = await UserService(session).count_all()

    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    builder = InlineKeyboardBuilder()

    for u in users:
        status = "🚫" if bool(u.is_banned) else "✅"
        uname = f"@{u.username}" if u.username else f"id:{u.id}"
        label = f"{status} {u.full_name[:16]} ({uname[:12]})"
        builder.row(InlineKeyboardButton(text=label, callback_data=f"adm:user:{u.id}"))

    nav_btns = []
    if page > 0:
        nav_btns.append(
            InlineKeyboardButton(text="◀️", callback_data=f"adm:users:page:{page - 1}")
        )
    nav_btns.append(
        InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="adm:noop")
    )
    if page < total_pages - 1:
        nav_btns.append(
            InlineKeyboardButton(text="▶️", callback_data=f"adm:users:page:{page + 1}")
        )
    if nav_btns:
        builder.row(*nav_btns)

    builder.row(
        InlineKeyboardButton(text="🔍 Поиск", callback_data="adm:search"),
        InlineKeyboardButton(text="◀️ Назад", callback_data="adm:back"),
    )

    text = f"👥 <b>Пользователи</b> (всего: {total})\nСтраница {page + 1}/{total_pages}\n\n"
    for u in users:
        status = "🚫" if bool(u.is_banned) else "✅"
        uname = f"@{u.username}" if u.username else f"<code>{u.id}</code>"
        safe_name = (
            (u.full_name or "—")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        text += (
            f"{status} <b>{safe_name}</b> ({uname}) — {float(u.balance or 0):.0f}₽\n"
        )

    try:
        await callback.message.edit_text(
            text, reply_markup=builder.as_markup(), parse_mode="HTML"
        )
    except Exception:
        pass


@router.callback_query(F.data == "adm:noop")
async def admin_noop(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(F.data.startswith("adm:user:"))
async def admin_user_detail(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        return
    await callback.answer()
    user_id = int(callback.data.split(":")[2])
    await _show_user_detail(callback, user_id)


# ── Search ────────────────────────────────────────────────────────────────────


@router.callback_query(F.data == "adm:search")
async def admin_search_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(callback.from_user.id):
        return
    await state.set_state(SearchState.waiting_query)
    await callback.message.edit_text(
        "🔍 <b>Поиск пользователя</b>\n\nВведите имя, @username или Telegram ID:",
        reply_markup=_back_admin_kb(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(SearchState.waiting_query)
async def admin_search_result(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        return
    await state.clear()
    query = message.text.strip().lstrip("@")

    from app.utils.html_utils import sanitize_search_query

    safe_query = sanitize_search_query(query, max_length=50)

    async with AsyncSessionFactory() as session:
        from sqlalchemy import select, or_
        from app.models.user import User

        if safe_query.isdigit():
            result = await session.execute(
                select(User).where(User.id == int(safe_query))
            )
            users = list(result.scalars().all())
        else:
            q = f"%{safe_query.lower()}%"
            result = await session.execute(
                select(User)
                .where(
                    or_(
                        User.username.ilike(q),
                        User.full_name.ilike(q),
                    )
                )
                .limit(10)
            )
            users = list(result.scalars().all())

    if not users:
        await message.answer(
            "❌ Пользователи не найдены.", reply_markup=_back_admin_kb()
        )
        return

    builder = InlineKeyboardBuilder()
    for u in users:
        status = "🚫" if bool(u.is_banned) else "✅"
        uname = f"@{u.username}" if u.username else f"id:{u.id}"
        builder.row(
            InlineKeyboardButton(
                text=f"{status} {u.full_name[:20]} ({uname})",
                callback_data=f"adm:user:{u.id}",
            )
        )
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="adm:back"))

    await message.answer(
        f"🔍 Найдено: <b>{len(users)}</b>\n\nВыберите пользователя:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )


# ── Ban/Unban ─────────────────────────────────────────────────────────────────


@router.callback_query(F.data.startswith("adm:ban:"))
async def admin_ban_user(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        return
    user_id = int(callback.data.split(":")[2])
    if (
        user_id == callback.from_user.id
        or user_id in config.telegram.telegram_admin_ids
    ):
        await callback.answer("❌ Нельзя забанить администратора", show_alert=True)
        return
    async with AsyncSessionFactory() as session:
        await UserService(session).ban(user_id)
        await session.commit()
        from app.services.bot_settings import BotSettingsService

        ban_msg = (
            await BotSettingsService(session).get("ban_message")
            or "🚫 Ваш аккаунт заблокирован."
        )
    from app.services.telegram_notify import TelegramNotifyService

    await TelegramNotifyService().send_message(user_id, ban_msg)
    await callback.answer("✅ Заблокирован", show_alert=True)
    await _show_user_detail(callback, user_id)


@router.callback_query(F.data.startswith("adm:unban:"))
async def admin_unban_user(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        return
    user_id = int(callback.data.split(":")[2])
    async with AsyncSessionFactory() as session:
        await UserService(session).unban(user_id)
        await session.commit()
        from app.services.bot_settings import BotSettingsService

        unban_msg = (
            await BotSettingsService(session).get("unban_message")
            or "✅ Ваш аккаунт разблокирован."
        )
    from app.services.telegram_notify import TelegramNotifyService

    await TelegramNotifyService().send_message(user_id, unban_msg)
    await callback.answer("✅ Разблокирован", show_alert=True)
    await _show_user_detail(callback, user_id)


# ── Balance ───────────────────────────────────────────────────────────────────


@router.callback_query(F.data.startswith("adm:addbal:"))
async def admin_addbal_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(callback.from_user.id):
        return
    user_id = int(callback.data.split(":")[2])
    await state.set_state(BalanceState.waiting_amount_add)
    await state.update_data(target_user_id=user_id)
    await callback.message.edit_text(
        f"💰 Введите сумму для пополнения баланса пользователя <code>{user_id}</code> (₽):",
        reply_markup=_back_admin_kb(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adm:deductbal:"))
async def admin_deductbal_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(callback.from_user.id):
        return
    user_id = int(callback.data.split(":")[2])
    await state.set_state(BalanceState.waiting_amount_deduct)
    await state.update_data(target_user_id=user_id)
    await callback.message.edit_text(
        f"💸 Введите сумму для снятия с баланса пользователя <code>{user_id}</code> (₽):",
        reply_markup=_back_admin_kb(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(BalanceState.waiting_amount_add)
async def admin_addbal_confirm(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        return
    try:
        from decimal import Decimal

        amount = Decimal(message.text.strip())
        if amount <= 0:
            raise ValueError
    except Exception:
        await message.answer("❌ Введите положительное число:")
        return
    data = await state.get_data()
    user_id = data["target_user_id"]
    await state.clear()
    async with AsyncSessionFactory() as session:
        user = await UserService(session).add_balance(user_id, amount)
        await session.commit()
    if user:
        from app.services.telegram_notify import TelegramNotifyService

        await TelegramNotifyService().send_message(
            user_id, f"💰 На ваш баланс зачислено <b>{amount} ₽</b>"
        )
        await message.answer(f"✅ Баланс пользователя {user_id} пополнен на {amount} ₽")
    else:
        await message.answer("❌ Пользователь не найден")
    text, kb = await _admin_main_text()
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.message(BalanceState.waiting_amount_deduct)
async def admin_deductbal_confirm(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        return
    try:
        from decimal import Decimal

        amount = Decimal(message.text.strip())
        if amount <= 0:
            raise ValueError
    except Exception:
        await message.answer("❌ Введите положительное число:")
        return
    data = await state.get_data()
    user_id = data["target_user_id"]
    await state.clear()
    async with AsyncSessionFactory() as session:
        user = await UserService(session).deduct_balance(user_id, amount)
        await session.commit()
    if user:
        from app.services.telegram_notify import TelegramNotifyService

        await TelegramNotifyService().send_message(
            user_id, f"💸 С вашего баланса списано <b>{amount} ₽</b>"
        )
        await message.answer(f"✅ С баланса пользователя {user_id} снято {amount} ₽")
    else:
        await message.answer("❌ Пользователь не найден или недостаточно средств")
    text, kb = await _admin_main_text()
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


# ── Keys ──────────────────────────────────────────────────────────────────────


@router.callback_query(F.data.startswith("adm:userkeys:"))
async def admin_user_keys(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        return
    await callback.answer()
    user_id = int(callback.data.split(":")[2])
    await _show_user_keys(callback, user_id)


@router.callback_query(F.data.startswith("adm:revokekey:"))
async def admin_revoke_key(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        return
    parts = callback.data.split(":")
    key_id, user_id = int(parts[2]), int(parts[3])
    async with AsyncSessionFactory() as session:
        key = await VpnKeyService(session).revoke(key_id)
        await session.commit()
    await callback.answer(
        f"✅ Ключ #{key_id} отозван" if key else "❌ Ключ не найден", show_alert=True
    )
    await _show_user_keys(callback, user_id)


@router.callback_query(F.data == "adm:keys")
async def admin_keys(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return

    async with AsyncSessionFactory() as session:
        keys = await VpnKeyService(session).get_all(limit=15)
        active_count = await VpnKeyService(session).count_active()

    lines = [f"🔑 <b>VPN ключи</b> (активных: {active_count})\n"]
    for k in keys:
        st = str(k.status.value if hasattr(k.status, "value") else k.status)
        icon = {"active": "✅", "revoked": "🚫", "expired": "⏰"}.get(st, "❓")
        exp = k.expires_at.strftime("%d.%m.%Y") if k.expires_at else "—"
        lines.append(
            f"{icon} #{k.id} user:{k.user_id} — {(k.name or '')[:20]} до {exp}"
        )

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🔄 Синхронизировать", callback_data="adm:sync_keys")
    )
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="adm:back"))

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "adm:sync_keys")
async def admin_sync_keys(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        return
    await callback.answer("🔄 Синхронизация...")
    async with AsyncSessionFactory() as session:
        result = await VpnKeyService(session).sync_from_marzban()
        await session.commit()
    await callback.message.edit_text(
        f"✅ Синхронизация завершена\n\nОбработано: {result['synced']}\nОшибок: {result['errors']}",
        reply_markup=_back_admin_kb(),
    )


# ── Gift key ──────────────────────────────────────────────────────────────────


@router.callback_query(F.data.startswith("adm:giftkey:plan:"))
async def admin_gift_key_confirm(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        return
    parts = callback.data.split(":")
    user_id, plan_id = int(parts[3]), int(parts[4])

    async with AsyncSessionFactory() as session:
        plan = await PlanService(session).get_by_id(plan_id)
        if not plan:
            await callback.answer("❌ Тариф не найден", show_alert=True)
            return
        key = await VpnKeyService(session).provision(user_id=user_id, plan=plan)
        await session.commit()

    if key:
        from app.services.telegram_notify import TelegramNotifyService

        await TelegramNotifyService().send_message(
            user_id,
            f"🎁 <b>Вам подарена подписка!</b>\n\n"
            f"Тариф: <b>{plan.name}</b> ({plan.duration_days} дней)\n\n"
            f"🔑 <b>Ссылка подписки:</b>\n<code>{key.access_url}</code>",
        )
        await callback.answer(f"✅ Ключ #{key.id} выдан", show_alert=True)
    else:
        await callback.answer("❌ Ошибка создания ключа в Marzban", show_alert=True)

    await _show_user_detail(callback, user_id)


@router.callback_query(F.data.startswith("adm:giftkey:"))
async def admin_gift_key_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(callback.from_user.id):
        return
    # Защита от попадания adm:giftkey:plan: сюда
    if callback.data.startswith("adm:giftkey:plan:"):
        return
    user_id = int(callback.data.split(":")[2])
    await state.update_data(gift_user_id=user_id)

    async with AsyncSessionFactory() as session:
        plans = await PlanService(session).get_all(only_active=True)

    if not plans:
        await callback.answer("❌ Нет активных тарифов", show_alert=True)
        return

    builder = InlineKeyboardBuilder()
    for plan in plans:
        builder.row(
            InlineKeyboardButton(
                text=f"🎁 {plan.name} — {plan.duration_days} дн.",
                callback_data=f"adm:giftkey:plan:{user_id}:{plan.id}",
            )
        )
    builder.row(
        InlineKeyboardButton(text="◀️ Отмена", callback_data=f"adm:user:{user_id}")
    )

    await callback.message.edit_text(
        f"🎁 Подарить ключ пользователю <code>{user_id}</code>\n\nВыберите тариф:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )
    await callback.answer()


# ── Message to user ───────────────────────────────────────────────────────────


class MsgState(StatesGroup):
    waiting_text = State()


@router.callback_query(F.data.startswith("adm:msg:"))
async def admin_msg_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(callback.from_user.id):
        return
    user_id = int(callback.data.split(":")[2])
    await state.set_state(MsgState.waiting_text)
    await state.update_data(msg_user_id=user_id)
    await callback.message.edit_text(
        f"✉️ Введите сообщение для пользователя <code>{user_id}</code> (HTML):",
        reply_markup=_back_admin_kb(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(MsgState.waiting_text)
async def admin_msg_send(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        return
    data = await state.get_data()
    user_id = data["msg_user_id"]
    await state.clear()
    from app.services.telegram_notify import TelegramNotifyService

    ok = await TelegramNotifyService().send_message(user_id, message.text)
    await message.answer(
        f"{'✅ Сообщение отправлено' if ok else '❌ Не удалось отправить'} пользователю {user_id}"
    )
    text, kb = await _admin_main_text()
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


# ── Tickets ───────────────────────────────────────────────────────────────────


@router.callback_query(F.data == "adm:tickets")
async def admin_tickets(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return

    async with AsyncSessionFactory() as session:
        tickets = await SupportService(session).get_all(limit=15)
        open_count = await SupportService(session).count_open()

    builder = InlineKeyboardBuilder()
    for tk in tickets[:10]:
        st = str(tk.status.value if hasattr(tk.status, "value") else tk.status)
        icon = {"open": "🔵", "in_progress": "🟡", "closed": "⚫"}.get(st, "❓")
        builder.row(
            InlineKeyboardButton(
                text=f"{icon} #{tk.id} — {tk.subject[:30]}",
                callback_data=f"adm:ticket:{tk.id}",
            )
        )
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="adm:back"))

    lines = [f"💬 <b>Тикеты поддержки</b> (открытых: {open_count})\n"]
    if not tickets:
        lines.append("Нет тикетов")

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adm:ticket:"))
async def admin_ticket_detail(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        return
    ticket_id = int(callback.data.split(":")[2])

    async with AsyncSessionFactory() as session:
        ticket = await SupportService(session).get_by_id(ticket_id)
        if not ticket:
            await callback.answer("Тикет не найден", show_alert=True)
            return
        subject = ticket.subject
        user_id = ticket.user_id
        st = str(
            ticket.status.value if hasattr(ticket.status, "value") else ticket.status
        )
        msgs = [
            {"is_admin": bool(m.is_admin), "text": m.text}
            for m in (ticket.messages[-5:] if ticket.messages else [])
        ]

    text = f"💬 <b>Тикет #{ticket_id}</b>\n📌 {subject}\n👤 User: {user_id}\n\n"
    for m in msgs:
        who = "🛡 Поддержка" if m["is_admin"] else "👤 Пользователь"
        text += f"<b>{who}:</b> {m['text'][:200]}\n\n"

    builder = InlineKeyboardBuilder()
    if st != "closed":
        builder.row(
            InlineKeyboardButton(
                text="✅ Закрыть", callback_data=f"adm:ticket:close:{ticket_id}"
            )
        )
    builder.row(InlineKeyboardButton(text="◀️ К тикетам", callback_data="adm:tickets"))

    await callback.message.edit_text(
        text, reply_markup=builder.as_markup(), parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adm:ticket:close:"))
async def admin_ticket_close(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        return
    ticket_id = int(callback.data.split(":")[3])
    async with AsyncSessionFactory() as session:
        from app.models.support import TicketStatus

        await SupportService(session).set_status(ticket_id, TicketStatus.CLOSED)
        await session.commit()
    await callback.answer("✅ Тикет закрыт", show_alert=True)
    await admin_tickets(callback)


# ── Payments ──────────────────────────────────────────────────────────────────


@router.callback_query(F.data == "adm:payments")
async def admin_payments(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return

    async with AsyncSessionFactory() as session:
        payments = await PaymentService(session).get_all(limit=10)
        revenue = await PaymentService(session).total_revenue()
        pending = await PaymentService(session).count_by_status(PaymentStatus.PENDING)

    lines = [
        f"💳 <b>Последние платежи</b>\n💰 Выручка: <b>{revenue} ₽</b> | ⏳ Ожидают: <b>{pending}</b>\n"
    ]
    for p in payments:
        st = str(p.status.value if hasattr(p.status, "value") else p.status)
        icon = {
            "succeeded": "✅",
            "pending": "⏳",
            "failed": "❌",
            "refunded": "↩️",
        }.get(st, "❓")
        prov = str(p.provider.value if hasattr(p.provider, "value") else p.provider)
        lines.append(
            f"{icon} #{p.id} user:{p.user_id} — <b>{p.amount} {p.currency}</b> ({prov})"
        )

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=_back_admin_kb(),
        parse_mode="HTML",
    )
    await callback.answer()


# ── Promos ────────────────────────────────────────────────────────────────────


@router.callback_query(F.data == "adm:promos")
async def admin_promos(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return

    async with AsyncSessionFactory() as session:
        promos = await PromoService(session).get_all()

    lines = [f"🎁 <b>Промокоды</b> (всего: {len(promos)})\n"]
    for p in promos[:15]:
        active = "✅" if bool(p.is_active) else "❌"
        uses = f"{p.current_uses}/{p.max_uses}" if p.max_uses else f"{p.current_uses}/∞"
        lines.append(
            f"{active} <code>{p.code}</code> — {p.promo_type} {p.value} ({uses})"
        )

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="➕ Создать промокод", callback_data="adm:promo:create"
        )
    )
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="adm:back"))

    await callback.message.edit_text(
        "\n".join(lines) if promos else "🎁 <b>Промокоды</b>\n\nПромокодов нет.",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "adm:promo:create")
async def admin_promo_create_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(callback.from_user.id):
        return
    await state.set_state(PromoCreateState.waiting_code)
    await callback.message.edit_text(
        "🎁 <b>Создание промокода</b>\n\nВведите код (латиница, заглавные):",
        reply_markup=_back_admin_kb(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(PromoCreateState.waiting_code)
async def promo_got_code(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        return
    code = message.text.strip().upper()
    await state.update_data(code=code)
    await state.set_state(PromoCreateState.waiting_type)
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="💰 Баланс (₽)", callback_data="promo_type:balance"),
        InlineKeyboardButton(text="📅 Дни", callback_data="promo_type:days"),
        InlineKeyboardButton(text="🏷 Скидка %", callback_data="promo_type:discount"),
    )
    await message.answer(
        f"Код: <code>{code}</code>\n\nВыберите тип бонуса:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("promo_type:"))
async def promo_got_type(callback: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(callback.from_user.id):
        return
    promo_type = callback.data.split(":")[1]
    await state.update_data(promo_type=promo_type)
    await state.set_state(PromoCreateState.waiting_value)
    labels = {
        "balance": "сумму в рублях (например: 100)",
        "days": "количество дней (например: 7)",
        "discount": "процент скидки (например: 20)",
    }
    await callback.message.edit_text(
        f"Введите {labels.get(promo_type, 'значение')}:", reply_markup=_back_admin_kb()
    )
    await callback.answer()


@router.message(PromoCreateState.waiting_value)
async def promo_got_value(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        return
    try:
        from decimal import Decimal

        value = Decimal(message.text.strip())
    except Exception:
        await message.answer("❌ Введите число:")
        return
    await state.update_data(value=str(value))
    await state.set_state(PromoCreateState.waiting_max_uses)
    await message.answer(
        "Максимальное количество использований (0 = безлимит):",
        reply_markup=_back_admin_kb(),
    )


@router.message(PromoCreateState.waiting_max_uses)
async def promo_got_max_uses(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        return
    try:
        max_uses = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Введите целое число:")
        return
    data = await state.get_data()
    await state.clear()
    from decimal import Decimal

    async with AsyncSessionFactory() as session:
        promo = await PromoService(session).create(
            code=data["code"],
            promo_type=data["promo_type"],
            value=Decimal(data["value"]),
            max_uses=max_uses,
        )
        await session.commit()
    await message.answer(
        f"✅ Промокод <code>{promo.code}</code> создан!\nТип: {promo.promo_type}, Значение: {promo.value}, Макс: {max_uses or '∞'}",
        parse_mode="HTML",
    )
    text, kb = await _admin_main_text()
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


# ── Broadcast ─────────────────────────────────────────────────────────────────


@router.callback_query(F.data == "adm:broadcast")
async def admin_broadcast_menu(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return

    async with AsyncSessionFactory() as session:
        broadcasts = await BroadcastService(session).get_all(limit=5)

    lines = ["📢 <b>Рассылки</b>\n"]
    for b in broadcasts:
        st = str(b.status.value if hasattr(b.status, "value") else b.status)
        icon = {"draft": "📝", "sending": "🔄", "done": "✅", "failed": "❌"}.get(
            st, "❓"
        )
        lines.append(f"{icon} {b.title[:30]} — {b.sent_count} отправлено")

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="📢 Создать рассылку", callback_data="adm:broadcast:create"
        )
    )
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="adm:back"))

    await callback.message.edit_text(
        "\n".join(lines) if broadcasts else "📢 <b>Рассылки</b>\n\nРассылок нет.",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "adm:broadcast:create")
async def admin_broadcast_create(callback: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(callback.from_user.id):
        return
    await state.set_state(BroadcastState.waiting_text)
    await callback.message.edit_text(
        "📢 <b>Новая рассылка</b>\n\nВведите текст сообщения (HTML поддерживается):",
        reply_markup=_back_admin_kb(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(BroadcastState.waiting_text)
async def broadcast_got_text(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        return
    await state.update_data(broadcast_text=message.text)
    await state.set_state(BroadcastState.waiting_target)

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="👥 Все", callback_data="bc_target:all"),
        InlineKeyboardButton(text="✅ Активные", callback_data="bc_target:active"),
    )
    builder.row(
        InlineKeyboardButton(text="⏰ Истёкшие", callback_data="bc_target:expired")
    )
    await message.answer(
        f"Текст:\n<i>{message.text[:200]}</i>\n\nВыберите аудиторию:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("bc_target:"))
async def broadcast_got_target(callback: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(callback.from_user.id):
        return
    target = callback.data.split(":")[1]
    data = await state.get_data()
    await state.clear()
    text = data.get("broadcast_text", "")

    async with AsyncSessionFactory() as session:
        bc = await BroadcastService(session).create(
            title=f"Рассылка от {callback.from_user.first_name}",
            text=text,
            target=target,
        )
        await session.commit()
        bc_id = bc.id

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="📤 Отправить сейчас", callback_data=f"adm:broadcast:send:{bc_id}"
        )
    )
    builder.row(InlineKeyboardButton(text="◀️ Отмена", callback_data="adm:broadcast"))

    target_labels = {"all": "Все", "active": "Активные", "expired": "Истёкшие"}
    await callback.message.edit_text(
        f"📢 Черновик создан!\n\nАудитория: <b>{target_labels.get(target, target)}</b>\n\nОтправить?",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adm:broadcast:send:"))
async def broadcast_send(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        return
    bc_id = int(callback.data.split(":")[3])
    await callback.answer("🔄 Запускаю рассылку...")
    async with AsyncSessionFactory() as session:
        bc = await BroadcastService(session).send(bc_id)
        await session.commit()
    if bc:
        await callback.message.edit_text(
            f"✅ Рассылка запущена!\n\nОтправлено: {bc.sent_count}\nОшибок: {bc.failed_count}",
            reply_markup=_back_admin_kb(),
        )
    else:
        await callback.message.edit_text(
            "❌ Ошибка запуска рассылки", reply_markup=_back_admin_kb()
        )


# ── Referrals ─────────────────────────────────────────────────────────────────


@router.callback_query(F.data == "adm:referrals")
async def admin_referrals(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return

    async with AsyncSessionFactory() as session:
        stats = await ReferralService(session).get_stats()
        top = await ReferralService(session).get_top(limit=10)

    lines = [
        f"👥 <b>Реферальная программа</b>\n",
        f"Всего рефералов: <b>{stats['total_referrals']}</b>",
        f"Оплачено бонусов: <b>{stats['paid_referrals']}</b>",
        f"Бонусных дней выдано: <b>{stats['total_bonus_days']}</b>\n",
        "<b>Топ рефереров:</b>",
    ]
    medals = ["🥇", "🥈", "🥉"] + [f"{i}." for i in range(4, 11)]
    for i, r in enumerate(top):
        medal = medals[i] if i < len(medals) else f"{i + 1}."
        uname = (
            f"@{r['username']}"
            if r.get("username")
            else r.get("full_name") or f"<code>{r['user_id']}</code>"
        )
        lines.append(f"{medal} {uname} — {r['referral_count']} реф.")

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=_back_admin_kb(),
        parse_mode="HTML",
    )
    await callback.answer()


# ── Groups ────────────────────────────────────────────────────────────────────


@router.callback_query(F.data == "adm:groups")
async def admin_groups(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    await callback.answer()

    import json as _json
    from app.services.bot_settings import BotSettingsService

    async with AsyncSessionFactory() as session:
        saved_raw = await BotSettingsService(session).get("vpn_group_ids")

    saved_ids: list[int] = []
    try:
        if saved_raw:
            saved_ids = [int(x) for x in _json.loads(saved_raw)]
    except Exception:
        pass

    await _show_groups(callback, saved_ids)


@router.callback_query(F.data.startswith("adm:group:toggle:"))
async def admin_group_toggle(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        return

    import json as _json
    from app.services.bot_settings import BotSettingsService

    gid = int(callback.data.split(":")[3])

    async with AsyncSessionFactory() as session:
        svc = BotSettingsService(session)
        saved_raw = await svc.get("vpn_group_ids")
        saved_ids: list[int] = []
        try:
            if saved_raw:
                saved_ids = [int(x) for x in _json.loads(saved_raw)]
        except Exception:
            pass

        if gid in saved_ids:
            saved_ids.remove(gid)
            action = "убрана"
        else:
            saved_ids.append(gid)
            action = "добавлена"

        await svc.set("vpn_group_ids", _json.dumps(saved_ids))
        await session.commit()

    await callback.answer(f"Группа {gid} {action}", show_alert=False)
    await _show_groups(callback, saved_ids)


# ── Commands ──────────────────────────────────────────────────────────────────


@router.message(Command("ban"))
async def ban_user_cmd(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        return
    args = message.text.split()
    if len(args) < 2 or not args[1].isdigit():
        await message.answer("Использование: /ban USER_ID")
        return
    user_id = int(args[1])
    async with AsyncSessionFactory() as session:
        user = await UserService(session).ban(user_id)
        await session.commit()
    await message.answer(
        f"✅ Пользователь {user_id} заблокирован."
        if user
        else f"❌ Пользователь {user_id} не найден."
    )


@router.message(Command("unban"))
async def unban_user_cmd(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        return
    args = message.text.split()
    if len(args) < 2 or not args[1].isdigit():
        await message.answer("Использование: /unban USER_ID")
        return
    user_id = int(args[1])
    async with AsyncSessionFactory() as session:
        user = await UserService(session).unban(user_id)
        await session.commit()
    await message.answer(
        f"✅ Пользователь {user_id} разблокирован."
        if user
        else f"❌ Пользователь {user_id} не найден."
    )


@router.message(Command("promo"))
async def create_promo_cmd(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        return
    args = message.text.split()
    if len(args) < 4:
        await message.answer(
            "/promo CODE TYPE VALUE [MAX_USES]\n"
            "TYPE: discount | balance | days\n"
            "Example: /promo SALE20 discount 20 100"
        )
        return
    code, promo_type, value_str = args[1], args[2], args[3]
    max_uses = int(args[4]) if len(args) > 4 else 0
    try:
        from decimal import Decimal

        async with AsyncSessionFactory() as session:
            promo = await PromoService(session).create(
                code=code.upper(),
                promo_type=promo_type.lower(),
                value=Decimal(value_str),
                max_uses=max_uses,
            )
            await session.commit()
        await message.answer(
            f"✅ Промокод <code>{promo.code}</code> создан!", parse_mode="HTML"
        )
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")


@router.message(Command("addbalance", "addbal"))
async def addbalance_cmd(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        return
    args = message.text.split()
    if len(args) < 3:
        await message.answer("ℹ️ Использование: /addbalance USER_ID AMOUNT")
        return
    try:
        user_id = int(args[1])
        from decimal import Decimal

        amount = Decimal(args[2])
    except Exception:
        await message.answer("❌ Неверные аргументы")
        return
    async with AsyncSessionFactory() as session:
        user = await UserService(session).add_balance(user_id, amount)
        await session.commit()
    if user:
        from app.services.telegram_notify import TelegramNotifyService

        await TelegramNotifyService().send_message(
            user_id, f"💰 На ваш баланс зачислено <b>{amount} ₽</b>"
        )
        await message.answer(f"✅ Баланс пользователя {user_id} пополнен на {amount} ₽")
    else:
        await message.answer("❌ Пользователь не найден")


@router.message(Command("givekey"))
async def givekey_cmd(message: Message) -> None:
    """Выдать ключ: /givekey USER_ID PLAN_ID"""
    if not _is_admin(message.from_user.id):
        return
    args = message.text.split()
    if len(args) < 3:
        await message.answer("ℹ️ Использование: /givekey USER_ID PLAN_ID")
        return
    try:
        user_id, plan_id = int(args[1]), int(args[2])
    except Exception:
        await message.answer("❌ Неверные аргументы")
        return
    async with AsyncSessionFactory() as session:
        plan = await PlanService(session).get_by_id(plan_id)
        if not plan:
            await message.answer(f"❌ Тариф {plan_id} не найден")
            return
        key = await VpnKeyService(session).provision(user_id=user_id, plan=plan)
        await session.commit()
    if key:
        from app.services.telegram_notify import TelegramNotifyService

        await TelegramNotifyService().send_message(
            user_id,
            f"🎁 <b>Вам выдана подписка!</b>\n\nТариф: <b>{plan.name}</b> ({plan.duration_days} дней)\n\n"
            f"🔑 <b>Ссылка:</b>\n<code>{key.access_url}</code>",
        )
        await message.answer(f"✅ Ключ #{key.id} выдан пользователю {user_id}")
    else:
        await message.answer("❌ Ошибка создания ключа в Marzban")


@router.message(F.photo)
async def get_file_id(message: Message) -> None:
    """Отправь фото боту — получишь file_id для вставки в панель."""
    if not _is_admin(message.from_user.id):
        return
    photo = message.photo[-1]
    await message.reply(
        f"📎 <b>file_id фото:</b>\n<code>{photo.file_id}</code>\n\n"
        f"Вставь это значение в панели: Telegram → Фото для разделов бота",
        parse_mode="HTML",
    )


@router.callback_query(F.data == "admin:panel")
async def show_admin_panel(callback: CallbackQuery) -> None:
    """Показать админ панель из главного меню."""
    if not _is_admin(callback.from_user.id):
        await callback.answer("❌ Доступ запрещён", show_alert=True)
        return
    async with AsyncSessionFactory() as session:
        from app.services.bot_settings import BotSettingsService

        panel_url = (await BotSettingsService(session).get("panel_url") or "").rstrip(
            "/"
        )
    await callback.message.edit_text(
        "🛡 <b>Админ панель</b>",
        reply_markup=admin_kb(panel_url=panel_url),
        parse_mode="HTML",
    )
    await callback.answer()
