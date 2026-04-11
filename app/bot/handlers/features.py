"""
Уникальные фичи VPN бота:
- /status    — статус всех подписок + дней осталось
- /ping      — пинг до VPN серверов (через Marzban nodes)
- /top       — топ рефереров (мотивация приглашать)
- /gift      — подарить подписку другу по username
- Автопродление подписки с баланса
"""
import asyncio
from datetime import datetime, timezone
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.core.database import AsyncSessionFactory
from app.services.vpn_key import VpnKeyService
from app.services.user import UserService
from app.services.referral import ReferralService
from app.services.bot_settings import BotSettingsService
from app.bot.utils.menu import get_main_menu_kb as _get_menu_kb

router = Router()


# ── /status — статус подписок ─────────────────────────────────────────────────

@router.message(Command("status"))
async def cmd_status(message: Message) -> None:
    async with AsyncSessionFactory() as session:
        keys = await VpnKeyService(session).get_active_for_user(message.from_user.id)

    if not keys:
        await message.answer(
            "📦 <b>Нет активных подписок</b>\n\nИспользуй /buy чтобы купить подписку.",
            parse_mode="HTML",
        )
        return

    now = datetime.now(timezone.utc)
    lines = ["📊 <b>Статус ваших подписок:</b>\n"]

    for k in keys:
        name = k.name or f"Подписка #{k.id}"
        if k.expires_at:
            delta = k.expires_at - now
            days = delta.days
            hours = delta.seconds // 3600
            if days > 7:
                time_str = f"✅ {days} дн."
                icon = "🟢"
            elif days > 0:
                time_str = f"⚠️ {days} дн. {hours} ч."
                icon = "🟡"
            else:
                time_str = f"🔴 {hours} ч."
                icon = "🔴"
            exp_str = k.expires_at.strftime("%d.%m.%Y")
            lines.append(f"{icon} <b>{name}</b>\n   До: {exp_str} ({time_str})")
        else:
            lines.append(f"🟢 <b>{name}</b>\n   Бессрочная")

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔑 Мои подписки", callback_data="my_keys"))
    builder.row(InlineKeyboardButton(text="💳 Продлить", callback_data="buy"))

    await message.answer("\n".join(lines), reply_markup=builder.as_markup(), parse_mode="HTML")


# ── /ping — пинг до серверов ──────────────────────────────────────────────────

@router.message(Command("ping", "серверы"))
async def cmd_ping(message: Message) -> None:
    msg = await message.answer("🔄 Проверяю серверы...")

    from app.services.pasarguard.pasarguard import get_vpn_panel
    try:
        panel = get_vpn_panel()
        # Only Marzban/Pasarguard has nodes endpoint
        from app.services.pasarguard.pasarguard import PasarguardService
        if isinstance(panel, PasarguardService):
            nodes_data = await PasarguardService().get_nodes()
            nodes = nodes_data.get("nodes", []) if isinstance(nodes_data, dict) else (nodes_data or [])
        else:
            nodes = []
    except Exception:
        nodes = []

    if not nodes:
        # Fallback: ping the main panel
        try:
            import httpx
            from app.core.config import config
            _pg = config.pasarguard
            if _pg:
                base = str(_pg.pasarguard_admin_panel).rstrip("/")
                ping_path = "/api/system"
            else:
                from app.core.configs.remnawave_config import remnawave as _rw
                base = (_rw.remnawave_url or "").rstrip("/")
                ping_path = "/api/system/stats"
            start = asyncio.get_event_loop().time()
            async with httpx.AsyncClient(timeout=5, verify=False) as client:
                await client.get(f"{base}{ping_path}")
            ms = int((asyncio.get_event_loop().time() - start) * 1000)
            status_icon = "🟢" if ms < 100 else "🟡" if ms < 300 else "🔴"
            text = f"🌐 <b>Статус серверов</b>\n\n{status_icon} Основной сервер: <b>{ms} мс</b>"
        except Exception:
            text = "🔴 <b>Сервер недоступен</b>"
    else:
        lines = ["🌐 <b>Статус серверов:</b>\n"]
        for node in nodes[:8]:
            status = node.get("status", "unknown")
            name = node.get("name", "Сервер")
            addr = node.get("address", "")
            icon = {"connected": "🟢", "connecting": "🟡", "error": "🔴", "disabled": "⚫"}.get(status, "❓")
            lines.append(f"{icon} <b>{name}</b>" + (f" — <code>{addr}</code>" if addr else ""))
        text = "\n".join(lines)

    try:
        await msg.edit_text(text, parse_mode="HTML")
    except Exception:
        await message.answer(text, parse_mode="HTML")


# ── /top — топ рефереров ──────────────────────────────────────────────────────

async def _build_top_text(user_id: int) -> str:
    async with AsyncSessionFactory() as session:
        top = await ReferralService(session).get_top(limit=10)
        my_count = await ReferralService(session).count_referrals(user_id)
        user = await UserService(session).get_by_id(user_id)
        ref_code = user.referral_code if user else None

    if not top:
        return "👥 Рейтинг пока пуст. Приглашай друзей первым!"

    medals = ["🥇", "🥈", "🥉"] + ["4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    lines = ["🏆 <b>Топ рефереров</b>\n"]

    for i, r in enumerate(top):
        medal = medals[i] if i < len(medals) else f"{i+1}."
        uname = f"@{r['username']}" if r.get("username") else r.get("full_name") or f"id:{r['referrer_id']}"
        is_me = " ← вы" if r["referrer_id"] == user_id else ""
        lines.append(f"{medal} {uname} — <b>{r['count']}</b> реф.{is_me}")

    lines.append(f"\n👤 Ваш результат: <b>{my_count}</b> рефералов")

    if ref_code:
        lines.append(f"\n🔗 Ваш реф. код: <code>{ref_code}</code>")

    return "\n".join(lines)


@router.message(Command("top", "рейтинг"))
async def cmd_top(message: Message) -> None:
    text = await _build_top_text(message.from_user.id)
    await message.answer(text, parse_mode="HTML")

@router.message(Command("gift"))
async def cmd_gift(message: Message) -> None:
    """
    /gift @username — подарить активную подписку другому пользователю.
    Списывает стоимость с баланса дарителя.
    """
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer(
            "🎁 <b>Подарить подписку</b>\n\n"
            "Использование: <code>/gift @username</code>\n\n"
            "Стоимость подарка списывается с вашего баланса.",
            parse_mode="HTML",
        )
        return

    target_username = args[1].lstrip("@").strip()

    async with AsyncSessionFactory() as session:
        from sqlalchemy import select
        from app.models.user import User
        result = await session.execute(
            select(User).where(User.username == target_username)
        )
        target = result.scalar_one_or_none()

        if not target:
            await message.answer(f"❌ Пользователь @{target_username} не найден в системе.")
            return

        if target.id == message.from_user.id:
            await message.answer("❌ Нельзя подарить подписку самому себе.")
            return

        sender = await UserService(session).get_by_id(message.from_user.id)
        balance = float(sender.balance or 0) if sender else 0.0

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(
        text=f"🎁 Подарить @{target_username}",
        callback_data=f"gift:confirm:{target.id}:{target_username}",
    ))
    builder.row(InlineKeyboardButton(text="◀️ Отмена", callback_data="back_main"))

    await message.answer(
        f"🎁 <b>Подарить подписку</b>\n\n"
        f"Получатель: @{target_username}\n"
        f"Ваш баланс: <b>{balance:.2f} ₽</b>\n\n"
        f"Выберите тариф для подарка:",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("gift:confirm:"))
async def gift_select_plan(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    target_id = int(parts[2])
    target_username = parts[3]

    async with AsyncSessionFactory() as session:
        from app.services.plan import PlanService
        plans = await PlanService(session).get_all(only_active=True)
        sender = await UserService(session).get_by_id(callback.from_user.id)
        balance = float(sender.balance or 0) if sender else 0.0

    builder = InlineKeyboardBuilder()
    for plan in plans:
        if balance >= float(plan.price):
            builder.row(InlineKeyboardButton(
                text=f"🎁 {plan.name} — {plan.price} ₽",
                callback_data=f"gift:buy:{target_id}:{plan.id}",
            ))
    builder.row(InlineKeyboardButton(text="◀️ Отмена", callback_data="back_main"))

    try:
        await callback.message.edit_text(
            f"🎁 Подарок для @{target_username}\n💰 Ваш баланс: {balance:.2f} ₽\n\nВыберите тариф:",
            reply_markup=builder.as_markup(),
            parse_mode="HTML",
        )
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("gift:buy:"))
async def gift_buy(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    target_id = int(parts[2])
    plan_id = int(parts[3])

    async with AsyncSessionFactory() as session:
        from app.services.plan import PlanService
        from app.services.vpn_key import VpnKeyService
        from app.services.telegram_notify import TelegramNotifyService

        plan = await PlanService(session).get_by_id(plan_id)
        if not plan:
            await callback.answer("Тариф не найден", show_alert=True)
            return

        sender = await UserService(session).deduct_balance(callback.from_user.id, plan.price)
        if not sender:
            await callback.answer("❌ Недостаточно средств на балансе", show_alert=True)
            return

        key = await VpnKeyService(session).provision(user_id=target_id, plan=plan)
        await session.commit()

        target = await UserService(session).get_by_id(target_id)
        target_name = f"@{target.username}" if target and target.username else f"id:{target_id}"

    if key:
        await TelegramNotifyService().send_message(
            target_id,
            f"🎁 <b>Вам подарена подписка!</b>\n\n"
            f"От: @{callback.from_user.username or callback.from_user.first_name}\n"
            f"Тариф: <b>{plan.name}</b> ({plan.duration_days} дней)\n\n"
            f"🔑 <b>Ссылка подписки:</b>\n<code>{key.access_url}</code>",
        )
        try:
            await callback.message.edit_text(
                f"✅ <b>Подарок отправлен!</b>\n\n"
                f"Получатель: {target_name}\n"
                f"Тариф: <b>{plan.name}</b>\n"
                f"Списано: <b>{plan.price} ₽</b>",
                parse_mode="HTML",
            )
        except Exception:
            pass
    else:
        await callback.answer("❌ Ошибка создания ключа", show_alert=True)

    await callback.answer()


# ── Автопродление ─────────────────────────────────────────────────────────────

@router.message(Command("autorenew", "автопродление"))
async def cmd_autorenew(message: Message) -> None:
    """Информация об автопродлении."""
    async with AsyncSessionFactory() as session:
        user = await UserService(session).get_by_id(message.from_user.id)
        balance = float(user.balance or 0) if user else 0.0
        keys = await VpnKeyService(session).get_active_for_user(message.from_user.id)

    if not keys:
        await message.answer("📦 Нет активных подписок для автопродления.")
        return

    lines = [
        "🔄 <b>Автопродление подписок</b>\n",
        f"💰 Ваш баланс: <b>{balance:.2f} ₽</b>\n",
        "При истечении подписки система автоматически спишет стоимость с баланса и продлит её.\n",
        "<b>Ваши подписки:</b>",
    ]
    for k in keys:
        exp = k.expires_at.strftime("%d.%m.%Y") if k.expires_at else "—"
        price = f"{k.price} ₽" if k.price else "—"
        lines.append(f"• {k.name or f'Подписка #{k.id}'} — до {exp}, цена: {price}")

    lines.append("\n💡 Пополни баланс чтобы автопродление работало.")

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="💰 Пополнить баланс", callback_data="buy"))
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="back_main"))

    await message.answer("\n".join(lines), reply_markup=builder.as_markup(), parse_mode="HTML")


@router.callback_query(F.data == "top_referrers")
async def cb_top(callback: CallbackQuery) -> None:
    await callback.answer()
    text = await _build_top_text(callback.from_user.id)
    try:
        await callback.message.edit_text(text, parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, parse_mode="HTML")


@router.callback_query(F.data == "status_cmd")
async def cb_status(callback: CallbackQuery) -> None:
    await callback.answer()
    await cmd_status(callback.message)
