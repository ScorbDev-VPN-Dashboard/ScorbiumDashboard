from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bot_settings import BotSettings

DEFAULTS = {
    "welcome_message": "👋 Привет, {name}!\n\nЭто VPN-бот. Выбери действие:",
    "btn_my_keys": "🔑 Мои ключи",
    "btn_buy": "💳 Купить подписку",
    "btn_support": "💬 Поддержка",
    "btn_balance": "💰 Баланс",
    "btn_promo": "🎁 Промокод",
    "support_url": "",
    "referral_bonus_days": "3",
    "referral_bonus_type": "days",
    "referral_bonus_value": "3",
    "payment_success_message": "✅ Оплата прошла успешно!\n\nВаш VPN-ключ готов. Нажмите «Мои ключи».",
    "ban_message": "🚫 Ваш аккаунт заблокирован. Обратитесь в поддержку.",
    "bot_disabled_message": "🔧 Бот временно отключён. Попробуйте позже.",
    "subscription_issued_message": "🔑 Ваш VPN-ключ выдан!\n\nНажмите «Мои ключи» для просмотра.",
    "subscription_cancelled_message": "❌ Ваша подписка была отменена.",
    "referral_welcome_message": "🎉 По вашей реферальной ссылке зарегистрировался новый пользователь!\n\nВам начислен бонус.",
    "unban_message": "✅ Ваш аккаунт разблокирован. Добро пожаловать обратно!",
    "bot_enabled": "1",
    "about_text": "",
    "vpn_group_ids": "",
    "required_channel_id": "",
    "required_channel_name": "",
    # ── Фото для разделов бота ────────────────────────────────────────────────
    "photo_welcome": "",
    "photo_buy": "",
    "photo_my_keys": "",
    "photo_balance": "",
    "photo_about": "",
    "photo_support": "",
    "photo_profile": "",
    "photo_language": "",
    "photo_trial": "",
    "panel_url": "",
    "keyboard_layout": "",  # JSON раскладка главного меню
    "bot_language": "ru",  # Язык бота: ru | en | fa
    "cryptobot_token": "",  # CryptoBot API токен
    "stars_rate": "1.5",  # Курс: 1 Star = X рублей
    # ── Платёжные системы — включение/отключение ──────────────────────────────
    "ps_yookassa_enabled": "0",
    "ps_cryptobot_enabled": "0",
    "ps_stars_enabled": "1",
    "ps_freekassa_enabled": "0",
    "ps_aikassa_enabled": "0",
    "ps_sbp_enabled": "0",
    # ── FreeKassa ─────────────────────────────────────────────────────────────
    "freekassa_shop_id": "",
    "freekassa_api_key": "",
    "freekassa_secret_word_1": "",
    "freekassa_secret_word_2": "",
    # ── AiKassa ───────────────────────────────────────────────────────────────
    "aikassa_shop_id": "",
    "aikassa_token": "",
    # ── Пробный период ────────────────────────────────────────────────────────
    "trial_enabled": "0",  # 1 = включён
    "trial_days": "3",  # кол-во дней пробного периода
    "trial_label": "🎁 Пробный период ({days} дн.)",  # текст кнопки
    # ── Уведомления об истечении подписки ─────────────────────────────────────
    "notify_expiry_enabled": "1",  # 1 = включены уведомления
    "notify_expiry_days": "7,3,1",  # за сколько дней уведомлять (через запятую)
    "notify_expiry_message": "⚠️ <b>Подписка истекает через {days} дн.!</b>\n\n📦 {name}\n📅 Дата истечения: <b>{date}</b>\n\nПродлите подписку чтобы не потерять доступ.",
    # ── Стили inline кнопок ───────────────────────────────────────────────────
    "btn_style_buy": "success",
    "btn_style_my_keys": "primary",
    "btn_style_support": "",
    "btn_style_balance": "",
    "btn_style_promo": "",
    "btn_style_back": "",
    "btn_style_profile": "",
    "btn_style_connect": "",
    "btn_style_about": "",
    "btn_style_servers": "",
    "btn_style_top_referrers": "",
    "btn_style_status": "",
    "btn_style_language": "",
    # ── Emergency Mute All ────────────────────────────────────────────────────
    "mute_all_enabled": "0",  # 1 = mute all active, stop key provisioning
    "mute_all_message": "⛔️ Ведутся технические работы. Напишите через час.",
    # ── Traffic Abuse Analysis ─────────────────────────────────────────
    "traffic_abuse_threshold_gb": "500",  # GB per day threshold for abuse alert
    "traffic_abuse_speed_limit_mbps": "10",  # Speed limit in Mbps when triggered
    # ── Custom emoji ID для кнопок (Premium) ─────────────────────────────────
    "btn_emoji_buy": "",
    "btn_emoji_my_keys": "",
    "btn_emoji_support": "",
    "btn_emoji_balance": "",
    "btn_emoji_promo": "",
    "btn_emoji_profile": "",
    "btn_emoji_connect": "",
    "btn_emoji_about": "",
    "btn_emoji_servers": "",
    "btn_emoji_top_referrers": "",
    "btn_emoji_status": "",
    "btn_emoji_language": "",
}


class BotSettingsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, key: str) -> Optional[str]:
        result = await self.session.execute(
            select(BotSettings).where(BotSettings.key == key)
        )
        row = result.scalar_one_or_none()
        if row:
            return row.value
        return DEFAULTS.get(key)

    async def get_all(self) -> dict:
        result = await self.session.execute(select(BotSettings))
        rows = {r.key: r.value for r in result.scalars().all()}

        merged = dict(DEFAULTS)
        merged.update(rows)
        return merged

    async def set(self, key: str, value: str) -> None:
        result = await self.session.execute(
            select(BotSettings).where(BotSettings.key == key)
        )
        row = result.scalar_one_or_none()
        if row:
            row.value = value
        else:
            self.session.add(BotSettings(key=key, value=value))
        await self.session.flush()

    async def set_many(self, data: dict) -> None:
        for key, value in data.items():
            await self.set(key, value)

    async def is_mute_all_enabled(self) -> bool:
        value = await self.get("mute_all_enabled")
        return value == "1"

    async def set_mute_all(self, enabled: bool) -> None:
        await self.set("mute_all_enabled", "1" if enabled else "0")

    async def get_traffic_abuse_threshold(self) -> int:
        value = await self.get("traffic_abuse_threshold_gb")
        return int(value) if value else 500

    async def get_traffic_abuse_speed_limit(self) -> int:
        value = await self.get("traffic_abuse_speed_limit_mbps")
        return int(value) if value else 10


async def create_traffic_analysis_service() -> "TrafficAnalysisService":
    from app.services.pasarguard.pasarguard import get_vpn_panel

    class TrafficAnalysisService:
        def __init__(self):
            self._panel = get_vpn_panel()

        async def get_all_users_with_traffic(self) -> dict:
            """Get all users with their traffic statistics."""
            all_users = []
            offset = 0
            limit = 200

            while True:
                data = await self._panel.get_users(offset=offset, limit=limit)
                users = data.get("users", [])
                if not users:
                    break

                for u in users:
                    username = u.get("username", "")
                    download = u.get("download", 0)
                    upload = u.get("upload", 0)
                    upload_total = upload + download
                    upload_gb = upload_total / (1024**3)
                    all_users.append(
                        {
                            "username": username,
                            "download": download,
                            "upload": upload,
                            "total_gb": upload_gb,
                            "email": u.get("email", ""),
                            "status": u.get("status", ""),
                        }
                    )

                offset += limit
                if len(users) < limit:
                    break

            return {"users": all_users}

        async def get_user_traffic(self, username: str) -> Optional[dict]:
            """Get traffic for a specific user."""
            try:
                user = await self._panel.get_user(username)
                if not user:
                    return None
                download = user.get("download", 0)
                upload = user.get("upload", 0)
                return {
                    "username": username,
                    "download": download,
                    "upload": upload,
                    "total_gb": (upload + download) / (1024**3),
                    "status": user.get("status", ""),
                }
            except Exception:
                return None

        async def apply_speed_limit(self, username: str, speed_mbps: int) -> bool:
            """Apply speed limit to a user (in Mbps)."""
            try:
                await self._panel.modify_user(
                    username,
                    **{"speed_limit": speed_mbps * 1024 * 1024 // 8},
                )
                return True
            except Exception:
                return False

    return TrafficAnalysisService()
