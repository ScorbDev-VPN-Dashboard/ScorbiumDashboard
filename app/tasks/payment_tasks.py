"""
Background task: периодически проверяет pending YooKassa платежи.
"""
import asyncio
import json
from datetime import datetime, timezone, timedelta
from sqlalchemy import select

from app.core.database import AsyncSessionFactory
from app.models.payment import Payment, PaymentProvider, PaymentStatus
from app.services.payment import PaymentService
from app.services.plan import PlanService
from app.services.vpn_key import VpnKeyService
from app.services.bot_settings import BotSettingsService
from app.services.telegram_notify import TelegramNotifyService
from app.utils.log import log

CHECK_INTERVAL = 60
MAX_PENDING_AGE = timedelta(hours=24)


async def check_pending_yookassa_payments() -> None:
    try:
        from app.services.yookassa import YookassaService
        yk = YookassaService()
    except Exception:
        return

    async with AsyncSessionFactory() as session:
        cutoff = datetime.now(timezone.utc) - MAX_PENDING_AGE
        result = await session.execute(
            select(Payment).where(
                Payment.status == PaymentStatus.PENDING,
                Payment.provider == PaymentProvider.YOOKASSA,
                Payment.external_id.isnot(None),
                Payment.created_at >= cutoff,
            )
        )
        payments = list(result.scalars().all())
        # detach to use outside session
        payment_data = [
            {"id": p.id, "external_id": p.external_id, "user_id": p.user_id, "meta": p.meta}
            for p in payments
        ]

    for pd in payment_data:
        try:
            yk_payment = yk.get_payment(pd["external_id"])
            if yk_payment.status == "succeeded":
                # Extract plan_id from meta
                plan_id = None
                if pd["meta"]:
                    try:
                        meta = json.loads(pd["meta"])
                        plan_id = int(meta.get("plan_id", 0)) or None
                    except Exception:
                        pass

                if not plan_id:
                    continue

                async with AsyncSessionFactory() as session:
                    plan = await PlanService(session).get_by_id(plan_id)
                    if not plan:
                        continue

                    payment = await PaymentService(session).get_by_id(pd["id"])
                    if not payment or payment.status == PaymentStatus.SUCCEEDED:
                        continue

                    payment.status = PaymentStatus.SUCCEEDED.value
                    await session.flush()

                    key = await VpnKeyService(session).provision(user_id=pd["user_id"], plan=plan)
                    if key:
                        payment.vpn_key_id = key.id
                    await session.commit()

                    settings = await BotSettingsService(session).get_all()
                    success_msg = settings.get("payment_success_message") or "✅ Оплата прошла успешно!"
                    if key:
                        text = (
                            f"{success_msg}\n\n"
                            f"🔑 <b>Ссылка подписки:</b>\n<code>{key.access_url}</code>\n\n"
                            f"📅 Действует <b>{plan.duration_days} дней</b>"
                        )
                    else:
                        text = f"{success_msg}\n\n⚠️ Не удалось создать ключ. Обратитесь в поддержку."
                    await TelegramNotifyService().send_message(pd["user_id"], text)
                    log.info(f"[polling] Payment {pd['id']} confirmed, key={key.id if key else None}")

            elif yk_payment.status in ("canceled", "expired"):
                async with AsyncSessionFactory() as session:
                    await PaymentService(session).fail(pd["id"])
                    await session.commit()

        except Exception as e:
            log.warning(f"[polling] Error checking payment {pd['id']}: {e}")


async def payment_polling_loop() -> None:
    log.info("💳 Payment polling task started")
    while True:
        await asyncio.sleep(CHECK_INTERVAL)
        await check_pending_yookassa_payments()
