from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
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
from app.models.payment import PaymentStatus

router = Router()


class PromoCreateState(StatesGroup):
    waiting_code = State()
    waiting_type = State()
    waiting_value = State()
    waiting_max_uses = State()


class BalanceState(StatesGroup):
    waiting_amount_add = State()
    waiting_amount_deduct = State()


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
    )
    builder.row(
        InlineKeyboardButton(text="🌐 Группы VPN", callback_data="adm:groups"),
    )
    if panel_url:
        from aiogram.types import WebAppInfo
        builder.row(
            InlineKeyboardButton(
                text="🖥 Открыть панель",
                web_app=WebAppInfo(url=panel_url),
            )
        )
    return builder.as_markup()


def _back_admin_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="adm:back"))
    return builder.as_markup()


async def _admin_main_text() -> tuple[str, InlineKeyboardMarkup]:
    async with AsyncSessionFactory() as session:
        total_users = await UserService(session).count_all()
        active_subs = await VpnKeyService(session).count_active()
        open_tickets = await SupportService(session).count_open()
        revenue = await PaymentService(session).total_revenue()
        from app.services.bot_settings import BotSettingsService
        panel_url = (await BotSettingsService(session).get("panel_url") or "").rstrip("/")

    # Build mini app URL with one-time token
    miniapp_url = ""
    if panel_url:
        import secrets as _sec
        import time as _t
        from app.api.panel.views import _miniapp_tokens
        token = _sec.token_urlsafe(32)
        _miniapp_tokens[token] = _t.time() + 300
        miniapp_url = f"{panel_url}/panel/miniapp-login?token={token}"

    text = (
        f"👑 <b>Админ-панель</b>\n\n"
        f"👥 Пользователей: <b>{total_users}</b>\n"
        f"✅ Активных подписок: <b>{active_subs}</b>\n"
        f"💬 Открытых тикетов: <b>{open_tickets}</b>\n"
        f"💰 Выручка: <b>{revenue} ₽</b>"
    )
    return text, admin_kb(panel_url=miniapp_url)


async def _show_user_detail(callback: CallbackQuery, user_id: int) -> None:
    async with AsyncSessionFactory() as session:
        user = await UserService(session).get_by_id(user_id)
        if not user:
            try:
                await callback.message.edit_text("Пользователь не найден", reply_markup=_back_admin_kb())
            except Exception:
                pass
            return
        keys = await VpnKeyService(session).get_all_for_user(user_id)
        is_banned = user.is_banned
        full_name = user.full_name
        username = user.username
        balance = float(user.balance or 0)
        active_keys = [k for k in keys if str(k.status.value if hasattr(k.status, "value") else k.status) == "active"]
        active_key = active_keys[0] if active_keys else None
        active_exp = active_key.expires_at.strftime('%d.%m.%Y') if active_key and active_key.expires_at else None
        keys_count = len(keys)

    uname = f"@{username}" if username else f"id:{user_id}"
    text = (
        f"👤 <b>{full_name}</b> ({uname})\n\n"
        f"Статус: {'🚫 Забанен' if is_banned else '✅ Активен'}\n"
        f"💰 Баланс: <b>{balance:.2f} ₽</b>\n"
        f"🔑 Подписок: {keys_count} (активных: {len(active_keys)})\n"
    )
    if active_exp:
        text += f"📅 Активна до: {active_exp}\n"

    builder = InlineKeyboardBuilder()
    if is_banned:
        builder.row(InlineKeyboardButton(text="✅ Разбанить", callback_data=f"adm:unban:{user_id}"))
    else:
        builder.row(InlineKeyboardButton(text="🚫 Забанить", callback_data=f"adm:ban:{user_id}"))
    builder.row(
        InlineKeyboardButton(text="💰 Пополнить баланс", callback_data=f"adm:addbal:{user_id}"),
        InlineKeyboardButton(text="💸 Снять баланс", callback_data=f"adm:deductbal:{user_id}"),
    )
    builder.row(InlineKeyboardButton(text="🔑 Ключи пользователя", callback_data=f"adm:userkeys:{user_id}"))
    builder.row(InlineKeyboardButton(text="◀️ К списку", callback_data="adm:users"))
    try:
        await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    except Exception:
        pass


async def _show_user_keys(callback: CallbackQuery, user_id: int) -> None:
    async with AsyncSessionFactory() as session:
        keys = await VpnKeyService(session).get_user_keys(user_id)

    builder = InlineKeyboardBuilder()
    if not keys:
        builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data=f"adm:user:{user_id}"))
        try:
            await callback.message.edit_text(
                f"🔑 У пользователя {user_id} нет активных ключей",
                reply_markup=builder.as_markup(),
            )
        except Exception:
            pass
        return

    lines = [f"🔑 <b>Ключи пользователя {user_id}</b>\n"]
    for k in keys:
        lines.append(f"#{k.id} — {(k.name or '')[:30]}")
        builder.row(InlineKeyboardButton(
            text=f"🚫 Отозвать #{k.id}",
            callback_data=f"adm:revokekey:{k.id}:{user_id}",
        ))
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data=f"adm:user:{user_id}"))
    try:
        await callback.message.edit_text("\n".join(lines), reply_markup=builder.as_markup(), parse_mode="HTML")
    except Exception:
        pass


async def _show_groups(callback: CallbackQuery, saved_ids: list[int]) -> None:
    from app.services.pasarguard.pasarguard import PasarguardService
    groups = await PasarguardService().get_groups()

    if not groups:
        try:
            await callback.message.edit_text(
                "🌐 <b>Группы VPN</b>\n\n❌ Не удалось загрузить группы из Marzban.",
                reply_markup=_back_admin_kb(), parse_mode="HTML",
            )
        except Exception:
            pass
        return

    lines = ["🌐 <b>Группы VPN (Marzban)</b>\n", "Нажми на группу чтобы включить/выключить:\n"]
    builder = InlineKeyboardBuilder()
    for g in groups:
        gid = g["id"]
        icon = "✅" if gid in saved_ids else "⬜"
        disabled = " 🔴" if g.get("is_disabled") else ""
        builder.row(InlineKeyboardButton(
            text=f"{icon} {g['name']}{disabled} ({g.get('total_users', 0)} юз.)",
            callback_data=f"adm:group:toggle:{gid}",
        ))
        inbounds = ", ".join(g.get("inbound_tags", []))
        lines.append(f"{icon} <b>{g['name']}</b> — {inbounds}")

    lines.append(f"\n💾 Активные: <code>{saved_ids}</code>" if saved_ids else "\n⚠️ Группы не выбраны")
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="adm:back"))
    try:
        await callback.message.edit_text("\n".join(lines), reply_markup=builder.as_markup(), parse_mode="HTML")
    except Exception:
        pass


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
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "adm:stats")
async def admin_stats(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return

    async with AsyncSessionFactory() as session:
        total_users = await UserService(session).count_all()
        active_subs = await VpnKeyService(session).count_active()
        open_tickets = await SupportService(session).count_open()
        revenue = await PaymentService(session).total_revenue()
        pending = await PaymentService(session).count_by_status(PaymentStatus.PENDING)

    await callback.message.edit_text(
        f"📊 <b>Статистика</b>\n\n"
        f"👥 Пользователей: <b>{total_users}</b>\n"
        f"✅ Активных подписок: <b>{active_subs}</b>\n"
        f"💬 Открытых тикетов: <b>{open_tickets}</b>\n"
        f"💰 Выручка: <b>{revenue} ₽</b>\n"
        f"⏳ Ожидают оплаты: <b>{pending}</b>",
        reply_markup=_back_admin_kb(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "adm:users")
async def admin_users(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return

    async with AsyncSessionFactory() as session:
        users = await UserService(session).get_all(limit=10)
        total = await UserService(session).count_all()

    lines = [f"👥 <b>Последние пользователи</b> (всего: {total})\n"]
    builder = InlineKeyboardBuilder()
    for u in users:
        status = "🚫" if u.is_banned else "✅"
        uname = f"@{u.username}" if u.username else f"id:{u.id}"
        lines.append(f"{status} {u.full_name} ({uname}) — 💰{float(u.balance or 0):.0f}₽")
        builder.row(InlineKeyboardButton(
            text=f"⚙️ {u.full_name[:20]}",
            callback_data=f"adm:user:{u.id}",
        ))
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="adm:back"))

    await callback.message.edit_text(
        "\n".join(lines), reply_markup=builder.as_markup(), parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adm:user:"))
async def admin_user_detail(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        return
    await callback.answer()
    user_id = int(callback.data.split(":")[2])
    await _show_user_detail(callback, user_id)


@router.callback_query(F.data.startswith("adm:ban:"))
async def admin_ban_user(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        return
    user_id = int(callback.data.split(":")[2])
    if user_id == callback.from_user.id:
        await callback.answer("❌ Нельзя забанить самого себя", show_alert=True)
        return
    if user_id in config.telegram.telegram_admin_ids:
        await callback.answer("❌ Нельзя забанить другого администратора", show_alert=True)
        return
    async with AsyncSessionFactory() as session:
        await UserService(session).ban(user_id)
        await session.commit()
    await callback.answer("✅ Пользователь заблокирован", show_alert=True)
    await _show_user_detail(callback, user_id)


@router.callback_query(F.data.startswith("adm:unban:"))
async def admin_unban_user(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        return
    user_id = int(callback.data.split(":")[2])
    async with AsyncSessionFactory() as session:
        await UserService(session).unban(user_id)
        await session.commit()
    await callback.answer("✅ Пользователь разблокирован", show_alert=True)
    await _show_user_detail(callback, user_id)


@router.callback_query(F.data.startswith("adm:addbal:"))
async def admin_addbal_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(callback.from_user.id):
        return
    user_id = int(callback.data.split(":")[2])
    await state.set_state(BalanceState.waiting_amount_add)
    await state.update_data(target_user_id=user_id)
    await callback.message.edit_text(
        f"💰 Введите сумму для пополнения баланса пользователя {user_id} (₽):",
        reply_markup=_back_admin_kb(),
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
        f"💸 Введите сумму для снятия с баланса пользователя {user_id} (₽):",
        reply_markup=_back_admin_kb(),
    )
    await callback.answer()


@router.message(BalanceState.waiting_amount_add)
async def admin_addbal_confirm(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        return
    try:
        from decimal import Decimal
        amount = Decimal(message.text.strip())
    except Exception:
        await message.answer("❌ Введите число:")
        return
    data = await state.get_data()
    user_id = data["target_user_id"]
    await state.clear()
    async with AsyncSessionFactory() as session:
        user = await UserService(session).add_balance(user_id, amount)
        await session.commit()
    if user:
        from app.services.telegram_notify import TelegramNotifyService
        await TelegramNotifyService().send_message(user_id, f"💰 На ваш баланс зачислено <b>{amount} ₽</b>")
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
    except Exception:
        await message.answer("❌ Введите число:")
        return
    data = await state.get_data()
    user_id = data["target_user_id"]
    await state.clear()
    async with AsyncSessionFactory() as session:
        user = await UserService(session).deduct_balance(user_id, amount)
        await session.commit()
    if user:
        from app.services.telegram_notify import TelegramNotifyService
        await TelegramNotifyService().send_message(user_id, f"💸 С вашего баланса списано <b>{amount} ₽</b>")
        await message.answer(f"✅ С баланса пользователя {user_id} снято {amount} ₽")
    else:
        await message.answer("❌ Пользователь не найден или недостаточно средств")
    text, kb = await _admin_main_text()
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


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
    await callback.answer(f"✅ Ключ #{key_id} отозван" if key else "❌ Ключ не найден", show_alert=True)
    await _show_user_keys(callback, user_id)


@router.callback_query(F.data == "adm:tickets")
async def admin_tickets(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return

    async with AsyncSessionFactory() as session:
        tickets = await SupportService(session).get_all(limit=10)
        open_count = await SupportService(session).count_open()

    lines = [f"💬 <b>Тикеты поддержки</b> (открытых: {open_count})\n"]
    for t in tickets[:10]:
        icon = {"open": "🔵", "in_progress": "🟡", "closed": "⚫"}.get(
            str(t.status.value if hasattr(t.status, "value") else t.status), "❓"
        )
        lines.append(f"{icon} #{t.id} — {t.subject[:35]}")

    if not tickets:
        lines.append("Нет тикетов")

    await callback.message.edit_text(
        "\n".join(lines), reply_markup=_back_admin_kb(), parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "adm:payments")
async def admin_payments(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return

    async with AsyncSessionFactory() as session:
        payments = await PaymentService(session).get_all(limit=10)
        revenue = await PaymentService(session).total_revenue()

    lines = [f"💳 <b>Последние платежи</b>\n💰 Выручка: {revenue} ₽\n"]
    for p in payments:
        st = str(p.status.value if hasattr(p.status, "value") else p.status)
        icon = {"succeeded": "✅", "pending": "⏳", "failed": "❌", "refunded": "↩️"}.get(st, "❓")
        prov = str(p.provider.value if hasattr(p.provider, "value") else p.provider)
        lines.append(f"{icon} #{p.id} — {p.amount} {p.currency} ({prov})")

    await callback.message.edit_text(
        "\n".join(lines), reply_markup=_back_admin_kb(), parse_mode="HTML",
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
        active = "✅" if p.is_active else "❌"
        uses = f"{p.current_uses}/{p.max_uses}" if p.max_uses else f"{p.current_uses}/∞"
        lines.append(f"{active} <code>{p.code}</code> — {p.promo_type} {p.value} ({uses})")

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="➕ Создать промокод", callback_data="adm:promo:create"))
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
        "🎁 <b>Создание промокода</b>\n\nВведите код промокода (латиница, заглавные):",
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
    labels = {"balance": "сумму в рублях (например: 100)", "days": "количество дней (например: 7)", "discount": "процент скидки (например: 20)"}
    await callback.message.edit_text(
        f"Введите {labels.get(promo_type, 'значение')}:",
        reply_markup=_back_admin_kb(),
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

    text, kb = await _admin_main_text()
    await message.answer(
        f"✅ Промокод <code>{promo.code}</code> создан!\n\n"
        f"Тип: {promo.promo_type}, Значение: {promo.value}, Макс. использований: {max_uses or '∞'}",
        parse_mode="HTML",
    )
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


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
    for i, r in enumerate(top, 1):
        uname = f"@{r['username']}" if r["username"] else r["full_name"] or f"id:{r['referrer_id']}"
        lines.append(f"{i}. {uname} — {r['count']} реф.")

    await callback.message.edit_text(
        "\n".join(lines), reply_markup=_back_admin_kb(), parse_mode="HTML",
    )
    await callback.answer()


# ── VPN Keys ──────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "adm:keys")
async def admin_keys(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return

    async with AsyncSessionFactory() as session:
        keys = await VpnKeyService(session).get_all(limit=15)

    lines = [f"🔑 <b>VPN ключи</b> (последние {len(keys)})\n"]
    for k in keys:
        st = str(k.status.value if hasattr(k.status, "value") else k.status)
        icon = {"active": "✅", "revoked": "🚫", "expired": "⏰"}.get(st, "❓")
        lines.append(f"{icon} #{k.id} user:{k.user_id} — {(k.name or '')[:25]}")

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="adm:back"))

    await callback.message.edit_text(
        "\n".join(lines), reply_markup=builder.as_markup(), parse_mode="HTML",
    )
    await callback.answer()


# ── Commands ──────────────────────────────────────────────────────────────────

@router.message(Command("ban"))
async def ban_user_cmd(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        return
    args = message.text.split()
    if len(args) < 2 or not args[1].isdigit():
        await message.answer("Использование: /ban <user_id>")
        return
    user_id = int(args[1])
    async with AsyncSessionFactory() as session:
        user = await UserService(session).ban(user_id)
        await session.commit()
    await message.answer(f"✅ Пользователь {user_id} заблокирован." if user else f"❌ Пользователь {user_id} не найден.")


@router.message(Command("unban"))
async def unban_user_cmd(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        return
    args = message.text.split()
    if len(args) < 2 or not args[1].isdigit():
        await message.answer("Использование: /unban <user_id>")
        return
    user_id = int(args[1])
    async with AsyncSessionFactory() as session:
        user = await UserService(session).unban(user_id)
        await session.commit()
    await message.answer(f"✅ Пользователь {user_id} разблокирован." if user else f"❌ Пользователь {user_id} не найден.")


@router.message(Command("promo"))
async def create_promo_cmd(message: Message) -> None:
    """Быстрое создание промокода: /promo CODE TYPE VALUE [MAX_USES]"""
    if not _is_admin(message.from_user.id):
        return
    args = message.text.split()
    if len(args) < 4:
        await message.answer(
            "Использование: /promo CODE TYPE VALUE [MAX_USES]\n"
            "TYPE: discount | balance | days\n"
            "Пример: /promo SALE20 discount 20 100"
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
        await message.answer(f"✅ Промокод <code>{promo.code}</code> создан!", parse_mode="HTML")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")


# ── VPN Groups ────────────────────────────────────────────────────────────────

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


# ── /getfileid — получить file_id фото ───────────────────────────────────────

@router.message(F.photo)
async def get_file_id(message: Message) -> None:
    """Отправь фото боту — получишь file_id для вставки в панель."""
    if not _is_admin(message.from_user.id):
        return
    photo = message.photo[-1]  # наибольшее разрешение
    await message.reply(
        f"📎 <b>file_id фото:</b>\n<code>{photo.file_id}</code>\n\n"
        f"Вставь это значение в панели: Telegram → Фото для разделов бота",
        parse_mode="HTML",
    )
