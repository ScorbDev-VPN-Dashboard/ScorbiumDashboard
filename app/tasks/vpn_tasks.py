import asyncio
from datetime import datetime, timezone

from app.core.database import AsyncSessionFactory
from app.services.vpn_key import VpnKeyService
from app.utils.log import log

EXPIRE_CHECK_INTERVAL = 300 
SYNC_INTERVAL = 3600 


async def expire_outdated_keys() -> None:
    """Mark expired VPN keys and disable them in Marzban."""
    try:
        async with AsyncSessionFactory() as session:
            count = await VpnKeyService(session).expire_outdated()
            await session.commit()
            if count:
                log.info(f"[vpn_tasks] Expired {count} outdated VPN keys")
    except Exception as e:
        log.error(f"[vpn_tasks] expire_outdated_keys error: {e}")


async def sync_keys_from_marzban() -> None:
    """Sync VPN key statuses from Marzban panel."""
    try:
        async with AsyncSessionFactory() as session:
            result = await VpnKeyService(session).sync_from_marzban()
            await session.commit()
            log.info(f"[vpn_tasks] Marzban sync: {result}")
    except Exception as e:
        log.error(f"[vpn_tasks] sync_keys_from_marzban error: {e}")


async def expire_loop() -> None:
    log.info("⏰ VPN expiry task started")
    while True:
        await asyncio.sleep(EXPIRE_CHECK_INTERVAL)
        await expire_outdated_keys()
        await notify_expiring_soon()
        await auto_renew_keys()


async def sync_loop() -> None:
    log.info("🔄 Marzban sync task started")
    await asyncio.sleep(60)
    while True:
        await sync_keys_from_marzban()
        await asyncio.sleep(SYNC_INTERVAL)


async def notify_expiring_soon() -> None:
    from datetime import datetime, timezone, timedelta
    from sqlalchemy import select
    from app.models.vpn_key import VpnKey, VpnKeyStatus
    from app.services.telegram_notify import TelegramNotifyService
    from app.services.bot_settings import BotSettingsService
    from app.services.user import UserService as _US

    try:
        async with AsyncSessionFactory() as session:
            settings = await BotSettingsService(session).get_all()

        # Проверяем включены ли уведомления
        if settings.get("notify_expiry_enabled", "1") != "1":
            return

        # Парсим периоды уведомлений
        raw_days = settings.get("notify_expiry_days", "7,3,1")
        notify_days = []
        for d in raw_days.split(","):
            try:
                notify_days.append(int(d.strip()))
            except ValueError:
                pass
        if not notify_days:
            return

        notify_msg_tpl = settings.get(
            "notify_expiry_message",
            "⚠️ <b>Подписка истекает через {days} дн.!</b>\n\n📦 {name}\n📅 Дата истечения: <b>{date}</b>\n\nПродлите подписку чтобы не потерять доступ.",
        )

        now = datetime.now(timezone.utc)
        notify = TelegramNotifyService()
        total_sent = 0

        for days_before in notify_days:
            # Окно: от (days_before дней - 5 мин) до (days_before дней + 5 мин)
            # Задача запускается каждые 5 минут, поэтому окно = интервал задачи
            window_start = now + timedelta(days=days_before) - timedelta(minutes=5)
            window_end = now + timedelta(days=days_before) + timedelta(minutes=5)

            async with AsyncSessionFactory() as session:
                result = await session.execute(
                    select(VpnKey).where(
                        VpnKey.status == VpnKeyStatus.ACTIVE.value,
                        VpnKey.expires_at >= window_start,
                        VpnKey.expires_at <= window_end,
                    )
                )
                keys = list(result.scalars().all())
                data = [
                    (k.user_id, k.name or f"Подписка #{k.id}", k.expires_at)
                    for k in keys
                ]

            for user_id, name, exp in data:
                # Проверяем бан
                async with AsyncSessionFactory() as check_session:
                    u = await _US(check_session).get_by_id(user_id)
                    if not u or u.is_banned:
                        continue
                    user_lang = u.language if u and u.language else None
                    from app.services.bot_settings import BotSettingsService as _BSS
                    s = await _BSS(check_session).get_all()
                    from app.services.i18n import get_lang
                    lang = get_lang(s, user_lang)

                exp_str = exp.strftime("%d.%m.%Y") if exp else "—"

                # Локализованные сообщения
                if lang == "en":
                    msg = (
                        f"⚠️ <b>Subscription expires in {days_before} day(s)!</b>\n\n"
                        f"📦 {name}\n📅 Expiry date: <b>{exp_str}</b>\n\n"
                        f"Renew your subscription to keep VPN access."
                    )
                elif lang == "fa":
                    msg = (
                        f"⚠️ <b>اشتراک شما در {days_before} روز منقضی می‌شود!</b>\n\n"
                        f"📦 {name}\n📅 تاریخ انقضا: <b>{exp_str}</b>\n\n"
                        f"اشتراک خود را تمدید کنید."
                    )
                else:
                    try:
                        msg = notify_msg_tpl.format(days=days_before, name=name, date=exp_str)
                    except Exception:
                        msg = f"⚠️ Подписка «{name}» истекает через {days_before} дн. ({exp_str})"

                await notify.send_message(user_id, msg)
                total_sent += 1

        if total_sent:
            log.info(f"[vpn_tasks] Sent {total_sent} expiry notifications")

    except Exception as e:
        log.error(f"[vpn_tasks] notify_expiring_soon error: {e}")


async def auto_renew_keys() -> None:
    from datetime import datetime, timezone, timedelta
    from sqlalchemy import select
    from app.models.vpn_key import VpnKey, VpnKeyStatus
    from app.models.user import User
    from app.services.user import UserService
    from app.services.vpn_key import VpnKeyService
    from app.services.telegram_notify import TelegramNotifyService
    from decimal import Decimal

    now = datetime.now(timezone.utc)
    expired_since = now - timedelta(hours=1)

    try:
        async with AsyncSessionFactory() as session:
            result = await session.execute(
                select(VpnKey).where(
                    VpnKey.status == VpnKeyStatus.ACTIVE.value,
                    VpnKey.expires_at >= expired_since,
                    VpnKey.expires_at <= now,
                    VpnKey.price.isnot(None),
                    VpnKey.plan_id.isnot(None),
                )
            )
            keys = list(result.scalars().all())
            data = [(k.id, k.user_id, k.plan_id, float(k.price or 0)) for k in keys]

        notify = TelegramNotifyService()
        for key_id, user_id, plan_id, price in data:
            if price <= 0:
                continue
            async with AsyncSessionFactory() as session:
                user = await UserService(session).deduct_balance(user_id, Decimal(str(price)))
                if not user:
                    await notify.send_message(
                        user_id,
                        "⚠️ <b>Автопродление не выполнено</b>\n\n"
                        "Недостаточно средств на балансе. Пополните баланс для продления подписки.",
                    )
                    continue

                from app.services.plan import PlanService
                plan = await PlanService(session).get_by_id(plan_id)
                if not plan:
                    continue

                key = await VpnKeyService(session).extend(key_id, plan.duration_days)
                await session.commit()

                if key:
                    exp_str = key.expires_at.strftime("%d.%m.%Y") if key.expires_at else "—"
                    await notify.send_message(
                        user_id,
                        f"✅ <b>Подписка автоматически продлена!</b>\n\n"
                        f"Списано: <b>{price} ₽</b>\n"
                        f"Действует до: <b>{exp_str}</b>",
                    )
                    log.info(f"[auto_renew] key={key_id} user={user_id} renewed for {plan.duration_days} days")

    except Exception as e:
        log.error(f"[auto_renew] error: {e}")
