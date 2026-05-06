"""Panel route modules — split from monolithic views.py for maintainability."""
from fastapi import APIRouter

from . import auth, dashboard, users, plans, payments
from . import subscriptions, promos, referrals, support, vpn
from . import broadcasts, telegram, backup, pasarguard, nodes
from . import exports, admins, keyboard, audit, monitoring, notifications

router = APIRouter()
router.include_router(auth.router)
router.include_router(dashboard.router)
router.include_router(plans.router)
router.include_router(payments.router)
router.include_router(subscriptions.router)
router.include_router(promos.router)
router.include_router(referrals.router)
router.include_router(support.router)
router.include_router(vpn.router)
router.include_router(broadcasts.router)
router.include_router(telegram.router)
router.include_router(backup.router)
router.include_router(pasarguard.router)
router.include_router(nodes.router)
router.include_router(exports.router)
router.include_router(admins.router)
router.include_router(keyboard.router)
router.include_router(audit.router)
router.include_router(monitoring.router)
router.include_router(notifications.router)
router.include_router(users.router)
