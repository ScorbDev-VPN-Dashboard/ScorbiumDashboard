from dataclasses import dataclass
from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.utils.menu import get_main_menu_kb as _get_menu_kb
from app.core.database import AsyncSessionFactory
from app.services.vpn_key import VpnKeyService
from app.services.bot_settings import BotSettingsService

router = Router()

CONNECT_GUIDES = {
    "ios": (
        "📱 <b>Подключение на iOS</b>\n\n"
        "1. Установи <b>Streisand</b> или <b>V2Box</b> из App Store\n"
        "2. Открой приложение → «+» → «Импорт из буфера обмена»\n"
        "3. Вставь свою ссылку подписки\n"
        "4. Нажми «Подключить» ✅\n\n"
        "💡 Рекомендуем: <b>Streisand</b> (бесплатно, без рекламы)"
    ),
    "android": (
        "🤖 <b>Подключение на Android</b>\n\n"
        "1. Установи <b>V2RayNG</b> из Google Play или APK\n"
        "2. Нажми «+» → «Импорт конфигурации из буфера обмена»\n"
        "3. Вставь ссылку подписки\n"
        "4. Нажми ▶️ для подключения ✅\n\n"
        "💡 Альтернатива: <b>Hiddify</b>"
    ),
    "windows": (
        "🖥 <b>Подключение на Windows</b>\n\n"
        "1. Скачай <b>Hiddify</b> или <b>v2rayN</b> с GitHub\n"
        "2. Открой → «Добавить подписку» → вставь ссылку\n"
        "3. Нажми «Обновить» → выбери сервер → «Подключить» ✅\n\n"
        "💡 Рекомендуем: <b>Hiddify Next</b>"
    ),
    "macos": (
        "🍎 <b>Подключение на macOS</b>\n\n"
        "1. Установи <b>FoXray</b> или <b>Hiddify</b> из Mac App Store\n"
        "2. Добавь подписку → вставь ссылку\n"
        "3. Выбери сервер → «Подключить» ✅\n\n"
        "💡 Альтернатива: <b>V2RayXS</b>"
    ),
    "linux": (
        "🐧 <b>Подключение на Linux</b>\n\n"
        "1. Установи <b>Hiddify</b>:\n"
        "<code>flatpak install flathub app.hiddify.com.HiddifyDesktop</code>\n\n"
        "2. Или используй <b>v2ray-core</b> + конфиг вручную\n"
        "3. Добавь ссылку подписки в приложение ✅\n\n"
        "💡 Для CLI: <b>sing-box</b>"
    ),
}


@dataclass
class KeyRow:
    id: int
    name: str
    status_val: str
    expires_str: str
    access_url: str
    price: str


# ── Мои подписки ──────────────────────────────────────────────────────────────

@router.callback_query(F.data == "my_keys")
async def show_my_keys(callback: CallbackQuery) -> None:
    async with AsyncSessionFactory() as session:
        all_keys = await VpnKeyService(session).get_all_for_user(callback.from_user.id)
        kb_menu = await _get_menu_kb(session)
        photo = await BotSettingsService(session).get("photo_my_keys")

        active_rows, archive_rows = [], []
        for k in all_keys:
            status_val = k.status.value if hasattr(k.status, "value") else str(k.status)
            exp = k.expires_at.strftime("%d.%m.%Y") if k.expires_at else "—"
            row = KeyRow(
                id=k.id, name=k.name or f"Подписка #{k.id}",
                status_val=status_val, expires_str=exp,
                access_url=k.access_url or "", price=str(k.price or ""),
            )
            if status_val == "active":
                active_rows.append(row)
            else:
                archive_rows.append(row)

    from app.bot.utils.media import edit_with_photo

    if not active_rows and not archive_rows:
        try:
            await edit_with_photo(
                callback,
                "📦 У тебя пока нет подписок.\n\nКупи подписку, чтобы получить VPN-доступ.",
                reply_markup=kb_menu,
                photo=photo or None,
            )
        except Exception:
            pass
        await callback.answer()
        return

    builder = InlineKeyboardBuilder()
    if active_rows:
        for row in active_rows:
            builder.row(InlineKeyboardButton(
                text=f"✅ {row.name} — до {row.expires_str}",
                callback_data=f"key:detail:{row.id}",
            ))
    else:
        builder.row(InlineKeyboardButton(text="😔 Нет активных подписок", callback_data="buy"))

    if archive_rows:
        builder.row(InlineKeyboardButton(
            text=f"🗂 Архив ({len(archive_rows)})",
            callback_data="key:archive",
        ))

    builder.row(
        InlineKeyboardButton(text="ℹ️ О проекте", callback_data="about"),
        InlineKeyboardButton(text="📲 Как подключить", callback_data="connect:menu"),
    )
    builder.row(InlineKeyboardButton(text="◀️ Главное меню", callback_data="back_main"))

    text = "📦 <b>Мои подписки</b>\n\n"
    if active_rows:
        text += f"✅ Активных: <b>{len(active_rows)}</b>\n"
    if archive_rows:
        text += f"🗂 В архиве: <b>{len(archive_rows)}</b>\n"

    try:
        await edit_with_photo(callback, text, reply_markup=builder.as_markup(), photo=photo or None)
    except Exception:
        pass
    await callback.answer()


# ── Архив ─────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "key:archive")
async def show_archive(callback: CallbackQuery) -> None:
    async with AsyncSessionFactory() as session:
        all_keys = await VpnKeyService(session).get_all_for_user(callback.from_user.id)

        archive_rows = []
        for k in all_keys:
            status_val = k.status.value if hasattr(k.status, "value") else str(k.status)
            if status_val != "active":
                exp = k.expires_at.strftime("%d.%m.%Y") if k.expires_at else "—"
                archive_rows.append(KeyRow(
                    id=k.id, name=k.name or f"Подписка #{k.id}",
                    status_val=status_val, expires_str=exp,
                    access_url="", price="",
                ))

    if not archive_rows:
        await callback.answer("Архив пуст", show_alert=True)
        return

    builder = InlineKeyboardBuilder()
    icons = {"expired": "⏰", "revoked": "❌"}
    for row in archive_rows:
        icon = icons.get(row.status_val, "❓")
        builder.row(InlineKeyboardButton(
            text=f"{icon} {row.name} — {row.expires_str}",
            callback_data=f"key:detail:{row.id}",
        ))
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="my_keys"))

    try:
        await callback.message.edit_text(
            f"🗂 <b>Архив подписок</b> ({len(archive_rows)}):",
            reply_markup=builder.as_markup(),
            parse_mode="HTML",
        )
    except Exception:
        pass
    await callback.answer()


# ── Детали ────────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("key:detail:"))
async def show_key_detail(callback: CallbackQuery) -> None:
    key_id = int(callback.data.split(":")[2])

    async with AsyncSessionFactory() as session:
        key = await VpnKeyService(session).get_by_id(key_id)
        if not key or key.user_id != callback.from_user.id:
            await callback.answer("Подписка не найдена", show_alert=True)
            return

        status_val = key.status.value if hasattr(key.status, "value") else str(key.status)
        exp = key.expires_at.strftime("%d.%m.%Y %H:%M") if key.expires_at else "—"
        name = key.name or f"Подписка #{key.id}"
        access_url = key.access_url or ""
        price = str(key.price or "")
        plan_name = key.plan.name if key.plan else name

    status_label = {
        "active": "✅ Активна", "expired": "⏰ Истекла", "revoked": "❌ Отозвана"
    }.get(status_val, "❓")

    text = (
        f"📦 <b>{plan_name}</b>\n\n"
        f"📊 Статус: {status_label}\n"
        f"📅 Действует до: <b>{exp}</b>\n"
    )
    if price:
        text += f"💰 Стоимость: <b>{price} ₽</b>\n"

    if access_url:
        text += f"\n🔑 <b>Ссылка подписки:</b>\n<code>{access_url}</code>\n\n💡 Скопируй и вставь в VPN-клиент"
    else:
        text += "\n⚠️ Ссылка недоступна."

    builder = InlineKeyboardBuilder()
    if access_url:
        builder.row(InlineKeyboardButton(text="📲 Как подключить?", callback_data="connect:menu"))
    back_cb = "my_keys" if status_val == "active" else "key:archive"
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data=back_cb))

    try:
        await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    except Exception:
        pass
    await callback.answer()


# ── О проекте ─────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "about")
async def about_project(callback: CallbackQuery) -> None:
    async with AsyncSessionFactory() as session:
        settings = await BotSettingsService(session).get_all()

    about_text = settings.get("about_text") or (
        "🌐 <b>О нашем VPN-сервисе</b>\n\n"
        "⚡️ Высокая скорость без ограничений\n"
        "🔒 Полная анонимность и шифрование\n"
        "🌍 Серверы в разных странах\n"
        "📱 Работает на всех устройствах\n"
        "🛡 Протоколы: VLESS, VMess, Shadowsocks\n\n"
        "💬 Поддержка 24/7 — всегда на связи\n"
        "🎁 Реферальная программа — приглашай друзей и получай бонусы"
    )
    photo = settings.get("photo_about") or None

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="📲 Как подключить", callback_data="connect:menu"))
    builder.row(InlineKeyboardButton(text="💳 Купить подписку", callback_data="buy"))
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="my_keys"))

    from app.bot.utils.media import edit_with_photo
    try:
        await edit_with_photo(callback, about_text, reply_markup=builder.as_markup(), photo=photo)
    except Exception:
        pass
    await callback.answer()


# ── Как подключить ────────────────────────────────────────────────────────────

@router.callback_query(F.data == "connect:menu")
async def connect_menu(callback: CallbackQuery) -> None:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📱 iOS", callback_data="connect:ios"),
        InlineKeyboardButton(text="🤖 Android", callback_data="connect:android"),
    )
    builder.row(
        InlineKeyboardButton(text="🖥 Windows", callback_data="connect:windows"),
        InlineKeyboardButton(text="🍎 macOS", callback_data="connect:macos"),
    )
    builder.row(InlineKeyboardButton(text="🐧 Linux", callback_data="connect:linux"))
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="my_keys"))

    try:
        await callback.message.edit_text(
            "📲 <b>Как подключить VPN?</b>\n\nВыбери своё устройство:",
            reply_markup=builder.as_markup(),
            parse_mode="HTML",
        )
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("connect:"))
async def connect_guide(callback: CallbackQuery) -> None:
    platform = callback.data.split(":")[1]
    if platform == "menu":
        return

    guide = CONNECT_GUIDES.get(platform)
    if not guide:
        await callback.answer("Инструкция не найдена", show_alert=True)
        return

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="◀️ Назад к устройствам", callback_data="connect:menu"))
    builder.row(InlineKeyboardButton(text="🔑 Мои подписки", callback_data="my_keys"))

    try:
        await callback.message.edit_text(guide, reply_markup=builder.as_markup(), parse_mode="HTML")
    except Exception:
        pass
    await callback.answer()
