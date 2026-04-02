from decimal import Decimal
from typing import Optional
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.payment import Payment, PaymentProvider, PaymentStatus
from app.models.plan import Plan


class PaymentService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, payment_id: int) -> Optional[Payment]:
        result = await self.session.execute(select(Payment).where(Payment.id == payment_id))
        return result.scalar_one_or_none()

    async def get_by_external_id(self, external_id: str) -> Optional[Payment]:
        result = await self.session.execute(
            select(Payment).where(Payment.external_id == external_id)
        )
        return result.scalar_one_or_none()

    async def get_all(
        self,
        limit: int = 100,
        offset: int = 0,
        status: Optional[PaymentStatus] = None,
        user_id: Optional[int] = None,
    ) -> list[Payment]:
        q = select(Payment).order_by(Payment.created_at.desc()).limit(limit).offset(offset)
        if status:
            q = q.where(Payment.status == status.value)
        if user_id:
            q = q.where(Payment.user_id == user_id)
        result = await self.session.execute(q)
        return list(result.scalars().all())

    async def total_revenue(self) -> Decimal:
        result = await self.session.execute(
            select(func.sum(Payment.amount)).where(
                Payment.status == PaymentStatus.SUCCEEDED.value
            )
        )
        return result.scalar_one() or Decimal("0")

    async def count_by_status(self, status: PaymentStatus) -> int:
        result = await self.session.execute(
            select(func.count()).where(Payment.status == status.value)
        )
        return result.scalar_one()

    async def create_pending(
        self,
        user_id: int,
        plan: Plan,
        provider: PaymentProvider,
        currency: str = "RUB",
    ) -> Payment:
        # Отменяем старые pending платежи этого юзера по тому же провайдеру
        from datetime import datetime, timezone, timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=15)
        old_result = await self.session.execute(
            select(Payment).where(
                Payment.user_id == user_id,
                Payment.status == PaymentStatus.PENDING.value,
                Payment.provider == provider.value,
            )
        )
        for old in old_result.scalars().all():
            old.status = PaymentStatus.FAILED.value

        payment = Payment(
            user_id=user_id,
            provider=provider.value,
            amount=plan.price,
            currency=currency,
            status=PaymentStatus.PENDING.value,
        )
        self.session.add(payment)
        await self.session.flush()
        return payment

    async def expire_old_pending(self, max_age_minutes: int = 15) -> int:
        """Отменяет pending платежи старше max_age_minutes минут. Возвращает кол-во отменённых."""
        from datetime import datetime, timezone, timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=max_age_minutes)
        result = await self.session.execute(
            select(Payment).where(
                Payment.status == PaymentStatus.PENDING.value,
                Payment.created_at <= cutoff,
            )
        )
        payments = result.scalars().all()
        count = 0
        for p in payments:
            p.status = PaymentStatus.FAILED.value
            count += 1
        if count:
            await self.session.flush()
        return count

    async def confirm(self, payment_id: int, external_id: str) -> Optional[Payment]:
        payment = await self.get_by_id(payment_id)
        if not payment:
            return None
        payment.status = PaymentStatus.SUCCEEDED.value
        payment.external_id = external_id
        await self.session.flush()
        return payment

    async def fail(self, payment_id: int) -> Optional[Payment]:
        payment = await self.get_by_id(payment_id)
        if payment:
            payment.status = PaymentStatus.FAILED.value
            await self.session.flush()
        return payment

    async def refund(self, payment_id: int) -> Optional[Payment]:
        payment = await self.get_by_id(payment_id)
        if payment:
            payment.status = PaymentStatus.REFUNDED.value
            await self.session.flush()
        return payment
