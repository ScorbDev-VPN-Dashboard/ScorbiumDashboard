from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from app.api.dependencies import get_db, get_current_admin
from app.models.payment import PaymentProvider, PaymentStatus
from app.schemas.payment import PaymentCreate, PaymentRead
from app.services.payment import PaymentService
from app.services.plan import PlanService
from app.utils.log import log

router = APIRouter()


@router.get("/", response_model=list[PaymentRead], summary="List payments")
async def list_payments(
    limit: int = 100,
    offset: int = 0,
    status: Optional[PaymentStatus] = None,
    user_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(get_current_admin),
) -> list[PaymentRead]:
    return await PaymentService(db).get_all(limit=limit, offset=offset, status=status, user_id=user_id)


@router.get("/{payment_id}", response_model=PaymentRead, summary="Get payment")
async def get_payment(
    payment_id: int,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(get_current_admin),
) -> PaymentRead:
    payment = await PaymentService(db).get_by_id(payment_id)
    if not payment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found")
    return payment


@router.post("/", response_model=PaymentRead, status_code=status.HTTP_201_CREATED, summary="Create pending payment")
async def create_payment(
    data: PaymentCreate,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(get_current_admin),
) -> PaymentRead:
    plan = await PlanService(db).get_by_id(data.plan_id)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    return await PaymentService(db).create_pending(data.user_id, plan, data.provider)


@router.post("/{payment_id}/refund", response_model=PaymentRead, summary="Refund payment")
async def refund_payment(
    payment_id: int,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(get_current_admin),
) -> PaymentRead:
    payment = await PaymentService(db).refund(payment_id)
    if not payment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found")
    return payment


@router.post("/webhook/freekassa", summary="FreeKassa webhook", include_in_schema=False)
async def freekassa_webhook(request: Request, db: AsyncSession = Depends(get_db)) -> str:
    """
    URL оповещения для FreeKassa.
    Укажи в личном кабинете: https://project.globaltunnel-vpn.cfd/api/v1/payments/webhook/freekassa
    """
    import ipaddress
    from app.services.freekassa import FreeKassaService
    from app.services.bot_settings import BotSettingsService

    # Проверка IP
    client_ip = request.headers.get("X-Real-IP") or request.client.host
    try:
        if ipaddress.ip_address(client_ip) not in {ipaddress.ip_address(ip) for ip in FreeKassaService.ALLOWED_IPS}:
            log.warning(f"FreeKassa webhook: blocked IP {client_ip}")
            return "FORBIDDEN"
    except Exception:
        log.warning(f"FreeKassa webhook: invalid IP {client_ip}")
        return "FORBIDDEN"

    form = await request.form()
    merchant_id = str(form.get("MERCHANT_ID", ""))
    amount = str(form.get("AMOUNT", ""))
    order_id = str(form.get("MERCHANT_ORDER_ID", ""))
    sign = str(form.get("SIGN", ""))

    # Получаем секретное слово 2 из БД
    settings = await BotSettingsService(db).get_all()
    fk = FreeKassaService.from_settings(settings)
    if not fk:
        log.error("FreeKassa webhook: service not configured")
        return "ERROR"

    # Проверяем подпись
    if not fk.verify_notification(merchant_id, amount, order_id, sign):
        log.warning(f"FreeKassa webhook: invalid sign for order {order_id}")
        return "WRONG SIGN"

    # order_id формат: "fk_{payment_id}_{plan_id}" или просто payment_id
    try:
        parts = order_id.split("_")
        if parts[0] == "fk" and len(parts) >= 3:
            payment_id = int(parts[1])
            plan_id = int(parts[2])
        else:
            log.error(f"FreeKassa webhook: unknown order_id format: {order_id}")
            return "YES"
    except (ValueError, IndexError):
        log.error(f"FreeKassa webhook: cannot parse order_id: {order_id}")
        return "YES"

    from app.services.plan import PlanService
    from app.services.vpn_key import VpnKeyService
    from app.services.telegram_notify import TelegramNotifyService

    plan = await PlanService(db).get_by_id(plan_id)
    payment = await PaymentService(db).get_by_id(payment_id)

    if not payment or not plan:
        log.error(f"FreeKassa webhook: payment {payment_id} or plan {plan_id} not found")
        return "YES"

    if payment.status == PaymentStatus.SUCCEEDED.value:
        return "YES"  # уже обработан

    payment.status = PaymentStatus.SUCCEEDED.value
    payment.external_id = str(form.get("intid", ""))
    await db.flush()

    key = await VpnKeyService(db).provision(user_id=payment.user_id, plan=plan)
    if key:
        payment.vpn_key_id = key.id
    await db.commit()

    success_msg = settings.get("payment_success_message", "✅ Оплата прошла успешно!")
    if key:
        text = f"{success_msg}\n\n🔑 <b>Ваш ключ:</b>\n<code>{key.access_url}</code>\n\n📅 Действует <b>{plan.duration_days} дней</b>"
    else:
        text = f"{success_msg}\n\nНажмите «Мои ключи» для получения ключа."

    await TelegramNotifyService().send_message(payment.user_id, text)
    log.info(f"FreeKassa: payment {payment_id} confirmed via webhook")
    return "YES"


@router.post("/webhook/yookassa", summary="Yookassa webhook", include_in_schema=False)
async def yookassa_webhook(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    try:
        body = await request.json()
        event = body.get("event")
        obj = body.get("object", {})
        external_id = obj.get("id")
        metadata = obj.get("metadata", {})
        payment_id = metadata.get("payment_id")
        plan_id = metadata.get("plan_id")

        if event == "payment.succeeded" and payment_id and plan_id:
            plan = await PlanService(db).get_by_id(int(plan_id))
            if plan:
                payment = await PaymentService(db).confirm(int(payment_id), external_id)
                await db.commit()

                if payment:
                    from app.services.vpn_key import VpnKeyService
                    key = await VpnKeyService(db).provision(user_id=payment.user_id, plan=plan)
                    if key:
                        payment.vpn_key_id = key.id
                    await db.commit()

                    # Уведомляем пользователя
                    from app.services.telegram_notify import TelegramNotifyService
                    from app.services.bot_settings import BotSettingsService
                    settings = await BotSettingsService(db).get_all()
                    success_msg = settings.get("payment_success_message", "✅ Оплата прошла успешно!")

                    if key:
                        text = f"{success_msg}\n\n🔑 <b>Ваш ключ:</b>\n<code>{key.access_url}</code>\n\n📅 Действует <b>{plan.duration_days} дней</b>"
                    else:
                        text = f"{success_msg}\n\nНажмите «Мои ключи» для получения ключа."

                    await TelegramNotifyService().send_message(payment.user_id, text)
                    log.info(f"Yookassa: payment {payment_id} confirmed, key provisioned")

        elif event == "payment.canceled" and payment_id:
            await PaymentService(db).fail(int(payment_id))
            await db.commit()

        return {"status": "ok"}
    except Exception as e:
        log.error(f"Yookassa webhook error: {e}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
