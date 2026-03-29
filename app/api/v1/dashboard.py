from decimal import Decimal
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from app.api.dependencies import get_db, get_current_admin
from app.services.user import UserService
from app.services.vpn_key import VpnKeyService
from app.services.payment import PaymentService
from app.services.support import SupportService
from app.services.telegram_notify import TelegramNotifyService
from app.models.payment import PaymentStatus

router = APIRouter()


class DashboardStats(BaseModel):
    total_users: int
    active_subscriptions: int
    total_revenue: Decimal
    pending_payments: int
    open_tickets: int
    bot_username: str | None = None


@router.get("/stats", response_model=DashboardStats, summary="Dashboard overview stats")
async def get_stats(
    db: AsyncSession = Depends(get_db),
    _: str = Depends(get_current_admin),
) -> DashboardStats:
    total_users = await UserService(db).count_all()
    active_subs = await VpnKeyService(db).count_active()
    revenue = await PaymentService(db).total_revenue()
    pending = await PaymentService(db).count_by_status(PaymentStatus.PENDING)
    open_tickets = await SupportService(db).count_open()
    bot_info = await TelegramNotifyService().get_bot_info()

    return DashboardStats(
        total_users=total_users,
        active_subscriptions=active_subs,
        total_revenue=revenue,
        pending_payments=pending,
        open_tickets=open_tickets,
        bot_username=bot_info.get("username") if bot_info else None,
    )
