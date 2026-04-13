import uuid
from decimal import Decimal
from typing import Optional

import yookassa
from yookassa import Payment as YKPayment
from yookassa.domain.response import PaymentResponse

from app.core.config import config
from app.core.exceptions import YookassaPaymentError
from app.utils.log import log


async def _get_yookassa_credentials() -> Optional[dict]:
    """
    Возвращает учётные данные ЮКассы.
    Приоритет: bot_settings (DB) → .env конфиг.
    Использует ORM — SQL-инъекции невозможны.
    """
    try:
        from app.core.database import AsyncSessionFactory
        from app.services.bot_settings import BotSettingsService
        async with AsyncSessionFactory() as session:
            svc = BotSettingsService(session)
            shop_id_str = await svc.get("yookassa_shop_id_override") or ""
            secret_key = await svc.get("yookassa_secret_key_override") or ""
            if shop_id_str and secret_key:
                return {"shop_id": int(shop_id_str), "secret_key": secret_key}
    except Exception as e:
        log.debug(f"YooKassa DB credentials lookup failed: {e}")

    # Fallback to .env
    if config.yookassa:
        auth = config.yookassa.get_auth
        if auth:
            return auth
    return None


def _configure_yookassa_sync(shop_id: int, secret_key: str) -> None:
    yookassa.Configuration.account_id = shop_id
    yookassa.Configuration.secret_key = secret_key


class YookassaService:
    def __init__(self, shop_id: Optional[int] = None, secret_key: Optional[str] = None) -> None:
        """
        Если shop_id/secret_key не переданы — используется _configure_yookassa() (env).
        Для async-инициализации из БД используй YookassaService.create().
        """
        if shop_id and secret_key:
            _configure_yookassa_sync(shop_id, secret_key)
            self._ready = True
        else:
            self._ready = _configure_yookassa_env()
            if not self._ready:
                raise YookassaPaymentError("Yookassa is not configured. Check YOOKASSA_SHOP_ID and YOOKASSA_SECRET_KEY.")

    @classmethod
    async def create(cls) -> "YookassaService":
        """Async factory — подхватывает настройки из БД или .env."""
        creds = await _get_yookassa_credentials()
        if not creds:
            raise YookassaPaymentError("Yookassa is not configured.")
        return cls(shop_id=creds["shop_id"], secret_key=creds["secret_key"])

    def create_payment(
        self,
        amount: Decimal,
        description: str,
        return_url: str,
        currency: str = "RUB",
        metadata: Optional[dict] = None,
        payment_method: Optional[str] = None,
    ) -> PaymentResponse:
        try:
            data: dict = {
                "amount": {"value": str(amount), "currency": currency},
                "confirmation": {"type": "redirect", "return_url": return_url},
                "capture": True,
                "description": description,
                "metadata": metadata or {},
            }
            if payment_method:
                data["payment_method_data"] = {"type": payment_method}
            payment = YKPayment.create(data, idempotency_key=str(uuid.uuid4()))
            log.info(f"Yookassa payment created: {payment.id}")
            return payment
        except Exception as e:
            log.error(f"Yookassa payment creation failed: {e}")
            raise YookassaPaymentError(str(e))

    def create_sbp_payment(
        self,
        amount: Decimal,
        description: str,
        return_url: str,
        metadata: Optional[dict] = None,
    ) -> PaymentResponse:
        return self.create_payment(
            amount=amount,
            description=description,
            return_url=return_url,
            metadata=metadata,
            payment_method="sbp",
        )

    def get_payment(self, payment_id: str) -> PaymentResponse:
        try:
            return YKPayment.find_one(payment_id)
        except Exception as e:
            raise YookassaPaymentError(str(e))

    @staticmethod
    def _sync_get_payment(payment_id: str) -> PaymentResponse:
        """Синхронная проверка платежа — используется в async контексте через await YookassaService.create()."""
        try:
            return YKPayment.find_one(payment_id)
        except Exception as e:
            raise YookassaPaymentError(str(e))

    def is_succeeded(self, payment_id: str) -> bool:
        return self.get_payment(payment_id).status == "succeeded"


def _configure_yookassa_env() -> bool:
    if not config.yookassa:
        return False
    auth = config.yookassa.get_auth
    if not auth:
        return False
    _configure_yookassa_sync(auth["shop_id"], auth["secret_key"])
    return True
