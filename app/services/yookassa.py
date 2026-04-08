import uuid
from decimal import Decimal
from typing import Optional

import yookassa
from yookassa import Payment as YKPayment
from yookassa.domain.response import PaymentResponse

from app.core.config import config
from app.core.exceptions import YookassaPaymentError
from app.utils.log import log


def _configure_yookassa() -> bool:
    if not config.yookassa:
        return False
    auth = config.yookassa.get_auth
    if not auth:
        return False
    yookassa.Configuration.account_id = auth["shop_id"]
    yookassa.Configuration.secret_key = auth["secret_key"]
    return True


class YookassaService:
    def __init__(self) -> None:
        if not _configure_yookassa():
            raise YookassaPaymentError("Yookassa is not configured. Check YOOKASSA_SHOP_ID and YOOKASSA_SECRET_KEY.")

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
        """Создать платёж через СБП (Система Быстрых Платежей)."""
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

    def is_succeeded(self, payment_id: str) -> bool:
        payment = self.get_payment(payment_id)
        return payment.status == "succeeded"
