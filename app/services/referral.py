from decimal import Decimal
from typing import Optional
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.referral import Referral, ReferralBonusType
from app.models.user import User


class ReferralService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_stats(self) -> dict:
        total = await self.session.execute(select(func.count()).select_from(Referral))
        paid = await self.session.execute(
            select(func.count()).select_from(Referral).where(Referral.is_paid == True)
        )
        # Sum bonus_value where bonus_type = days
        bonus_sum = await self.session.execute(
            select(func.sum(Referral.bonus_value)).where(Referral.bonus_type == ReferralBonusType.DAYS.value)
        )
        return {
            "total_referrals": total.scalar_one(),
            "paid_referrals": paid.scalar_one(),
            "total_bonus_days": int(bonus_sum.scalar_one() or 0),
        }

    async def get_top(self, limit: int = 20) -> list[dict]:
        result = await self.session.execute(
            select(
                Referral.referrer_id,
                User.username,
                User.full_name,
                func.count(Referral.id).label("count"),
            )
            .join(User, User.id == Referral.referrer_id)
            .group_by(Referral.referrer_id, User.username, User.full_name)
            .order_by(func.count(Referral.id).desc())
            .limit(limit)
        )
        return [
            {
                "referrer_id": row.referrer_id,
                "username": row.username,
                "full_name": row.full_name,
                "count": row.count,
                "bonus_days": 0,
            }
            for row in result.all()
        ]

    async def get_for_user(self, referrer_id: int) -> list[Referral]:
        result = await self.session.execute(
            select(Referral)
            .where(Referral.referrer_id == referrer_id)
            .order_by(Referral.created_at.desc())
        )
        return list(result.scalars().all())

    async def create(
        self,
        referrer_id: int,
        referred_id: int,
        bonus_type: str = "days",
        bonus_value: Decimal = Decimal("3"),
        # bonus_days kept for compat but ignored
        bonus_days: int = 0,
    ) -> Optional[Referral]:
        existing = await self.session.execute(
            select(Referral).where(Referral.referred_id == referred_id)
        )
        if existing.scalar_one_or_none():
            return None
        ref = Referral(
            referrer_id=referrer_id,
            referred_id=referred_id,
            bonus_type=bonus_type,
            bonus_value=bonus_value,
        )
        self.session.add(ref)
        await self.session.flush()
        return ref

    async def pay_bonus(self, referral_id: int) -> Optional[Referral]:
        result = await self.session.execute(select(Referral).where(Referral.id == referral_id))
        ref = result.scalar_one_or_none()
        if not ref or ref.is_paid:
            return ref

        from app.services.user import UserService
        user_svc = UserService(self.session)

        bonus_type = ref.bonus_type or "days"
        bonus_value = ref.bonus_value or Decimal("0")

        if bonus_type == ReferralBonusType.BALANCE.value:
            await user_svc.add_balance(ref.referrer_id, bonus_value)
        elif bonus_type == ReferralBonusType.DAYS.value:
            from app.models.vpn_key import VpnKey, VpnKeyStatus
            from app.services.pasarguard.pasarguard import PasarguardService
            key_result = await self.session.execute(
                select(VpnKey).where(
                    VpnKey.user_id == ref.referrer_id,
                    VpnKey.status == VpnKeyStatus.ACTIVE.value,
                )
            )
            key = key_result.scalar_one_or_none()
            if key and key.expires_at:
                from datetime import timedelta
                key.expires_at = key.expires_at + timedelta(days=int(bonus_value))
                if key.pasarguard_key_id:
                    try:
                        await PasarguardService().extend_user(key.pasarguard_key_id, int(bonus_value))
                    except Exception:
                        pass

        ref.is_paid = True
        await self.session.flush()
        return ref

    async def count_referrals(self, referrer_id: int) -> int:
        result = await self.session.execute(
            select(func.count()).select_from(Referral).where(Referral.referrer_id == referrer_id)
        )
        return result.scalar_one()
