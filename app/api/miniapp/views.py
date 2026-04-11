import hashlib
import hmac
import json
import urllib.parse
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db
from app.core.config import config
from app.models.payment import PaymentProvider, PaymentStatus
from app.schemas.user import UserCreate
from app.services.bot_settings import BotSettingsService
from app.services.payment import PaymentService
from app.services.plan import PlanService
from app.services.referral import ReferralService
from app.services.user import UserService
from app.services.vpn_key import VpnKeyService
from app.utils.log import log

router = APIRouter()

_tpl_path = Path(__file__).resolve().parent.parent.parent / "templates" / "miniapp"
_tpl_path.mkdir(exist_ok=True)
templates = Jinja2Templates(directory=str(_tpl_path))


def _verify_telegram_data(init_data: str) -> Optional[dict]:
    """Verify Telegram WebApp initData and return parsed user data."""
    try:
        parsed = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
        hash_val = parsed.pop("hash", "")

        data_check = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
        token = config.telegram.telegram_bot_token.get_secret_value()
        secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
        expected = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()

        if not hmac.compare_digest(expected, hash_val):
            return None

        user_data = json.loads(parsed.get("user", "{}"))
        return user_data
    except Exception as e:
        log.warning(f"Mini App auth error: {e}")
        return None


async def _get_tg_user(request: Request) -> Optional[dict]:
    """Extract and verify Telegram user from request."""
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    if not init_data:
        init_data = request.query_params.get("tgWebAppData", "")
    if not init_data:
        return None
    return _verify_telegram_data(init_data)


# ── Pages ─────────────────────────────────────────────────────────────────────


@router.get("/", response_class=HTMLResponse)
async def miniapp_index(request: Request, db: AsyncSession = Depends(get_db)):
    """Main Mini App page."""
    from app.core.config import config as _cfg

    settings = await BotSettingsService(db).get_all()
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "app_name": _cfg.web.app_name,
            "support_url": settings.get("support_url", ""),
            "about_text": settings.get("about_text", ""),
            "bot_language": settings.get("bot_language", "ru"),
        },
    )


# ── API endpoints ─────────────────────────────────────────────────────────────


@router.post("/auth")
async def miniapp_auth(request: Request, db: AsyncSession = Depends(get_db)):
    """Authenticate user via Telegram initData, create if not exists."""
    body = await request.json()
    init_data = body.get("initData", "")

    tg_user = _verify_telegram_data(init_data)
    if not tg_user:
        return JSONResponse({"ok": False, "error": "Invalid auth"}, status_code=401)

    user_id = tg_user.get("id")
    if not user_id:
        return JSONResponse({"ok": False, "error": "No user ID"}, status_code=401)

    user, _ = await UserService(db).get_or_create(
        UserCreate(
            id=user_id,
            username=tg_user.get("username"),
            full_name=f"{tg_user.get('first_name', '')} {tg_user.get('last_name', '')}".strip(),
        )
    )
    await db.commit()

    settings = await BotSettingsService(db).get_all()

    return JSONResponse(
        {
            "ok": True,
            "user": {
                "id": user.id,
                "username": user.username,
                "full_name": user.full_name,
                "balance": float(user.balance or 0),
                "referral_code": user.referral_code,
            },
            "lang": settings.get("bot_language", "ru"),
        }
    )


@router.get("/plans")
async def get_plans(db: AsyncSession = Depends(get_db)):
    """Get active plans."""
    plans = await PlanService(db).get_all(only_active=True)
    return JSONResponse(
        {
            "ok": True,
            "plans": [
                {
                    "id": p.id,
                    "name": p.name,
                    "price": float(p.price or 0),
                    "duration_days": p.duration_days,
                    "description": p.description,
                    "currency": p.currency,
                }
                for p in plans
            ],
        }
    )


@router.get("/profile")
async def get_profile(request: Request, db: AsyncSession = Depends(get_db)):
    """Get user profile with subscriptions."""
    tg_user = await _get_tg_user(request)
    if not tg_user:
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)

    user_id = tg_user["id"]
    user = await UserService(db).get_by_id(user_id)
    if not user:
        return JSONResponse({"ok": False, "error": "User not found"}, status_code=404)

    keys = await VpnKeyService(db).get_all_for_user(user_id)
    ref_count = await ReferralService(db).count_referrals(user_id)

    active_keys = []
    archive_keys = []
    for k in keys:
        status = str(k.status.value if hasattr(k.status, "value") else k.status)
        key_data = {
            "id": k.id,
            "name": k.name or f"Subscription #{k.id}",
            "status": status,
            "expires_at": k.expires_at.isoformat() if k.expires_at else None,
            "access_url": k.access_url if status == "active" else None,
            "price": float(k.price or 0),
        }
        if status == "active":
            active_keys.append(key_data)
        else:
            archive_keys.append(key_data)

    return JSONResponse(
        {
            "ok": True,
            "user": {
                "id": user.id,
                "full_name": user.full_name,
                "username": user.username,
                "balance": float(user.balance or 0),
                "referral_code": user.referral_code,
                "referrals_count": ref_count,
            },
            "active_keys": active_keys,
            "archive_keys": archive_keys,
        }
    )


@router.post("/pay/balance")
async def pay_balance(request: Request, db: AsyncSession = Depends(get_db)):
    """Pay with internal balance."""
    tg_user = await _get_tg_user(request)
    if not tg_user:
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)

    body = await request.json()
    plan_id = body.get("plan_id")
    user_id = tg_user["id"]

    plan = await PlanService(db).get_by_id(plan_id)
    if not plan or not plan.is_active:
        return JSONResponse({"ok": False, "error": "Plan not found"}, status_code=404)

    user = await UserService(db).get_by_id(user_id)
    if not user or float(user.balance or 0) < float(plan.price):
        return JSONResponse(
            {"ok": False, "error": "Insufficient balance"}, status_code=400
        )

    updated = await UserService(db).deduct_balance(user_id, plan.price)
    if not updated:
        return JSONResponse({"ok": False, "error": "Balance error"}, status_code=400)

    payment = await PaymentService(db).create_pending(
        user_id=user_id, plan=plan, provider=PaymentProvider.BALANCE
    )
    payment.status = PaymentStatus.SUCCEEDED.value
    await db.flush()

    key = await VpnKeyService(db).provision(user_id=user_id, plan=plan)
    if key:
        payment.vpn_key_id = key.id
    await db.commit()

    if key:
        return JSONResponse(
            {
                "ok": True,
                "access_url": key.access_url,
                "expires_at": key.expires_at.isoformat() if key.expires_at is not None else None,
                "plan_name": plan.name,
                "days": plan.duration_days,
            }
        )
    return JSONResponse(
        {"ok": False, "error": "Failed to create VPN key"}, status_code=500
    )


@router.post("/pay/yookassa")
async def pay_yookassa(request: Request, db: AsyncSession = Depends(get_db)):
    """Create YooKassa payment and return redirect URL."""
    tg_user = await _get_tg_user(request)
    if not tg_user:
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)

    body = await request.json()
    plan_id = body.get("plan_id")
    user_id = tg_user["id"]

    plan = await PlanService(db).get_by_id(plan_id)
    if not plan or not plan.is_active:
        return JSONResponse({"ok": False, "error": "Plan not found"}, status_code=404)

    try:
        from app.services.yookassa import YookassaService

        yk = YookassaService()
        payment = await PaymentService(db).create_pending(
            user_id=user_id, plan=plan, provider=PaymentProvider.YOOKASSA
        )
        await db.flush()

        return_url = f"https://t.me/{body.get('bot_username', '')}"
        yk_payment = yk.create_payment(
            amount=plan.price,
            description=f"VPN — {plan.name}",
            return_url=return_url,
            metadata={"payment_id": str(payment.id), "plan_id": str(plan.id)},
        )
        payment.external_id = yk_payment.id
        import json as _json

        payment.meta = _json.dumps({"plan_id": plan.id})
        await db.commit()

        return JSONResponse(
            {
                "ok": True,
                "payment_id": payment.id,
                "confirm_url": yk_payment.confirmation.confirmation_url if yk_payment.confirmation else None,
            }
        )
    except Exception as e:
        log.error(f"Mini App YooKassa error: {e}")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.post("/pay/sbp")
async def pay_sbp(request: Request, db: AsyncSession = Depends(get_db)):
    """Create SBP (YooKassa) payment."""
    tg_user = await _get_tg_user(request)
    if not tg_user:
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)

    body = await request.json()
    plan_id = body.get("plan_id")
    user_id = tg_user["id"]

    plan = await PlanService(db).get_by_id(plan_id)
    if not plan or plan.is_active is False:
        return JSONResponse({"ok": False, "error": "Plan not found"}, status_code=404)

    try:
        from app.models.payment import PaymentProvider
        from app.services.yookassa import YookassaService

        yk = YookassaService()
        payment = await PaymentService(db).create_pending(
            user_id=user_id, plan=plan, provider=PaymentProvider.YOOKASSA_SBP
        )
        await db.flush()
        import json as _json

        payment.meta = _json.dumps({"plan_id": plan.id})

        return_url = "https://t.me/"
        yk_payment = yk.create_sbp_payment(
            amount=plan.price,
            description=f"VPN — {plan.name}",
            return_url=return_url,
            metadata={"payment_id": str(payment.id), "plan_id": str(plan.id)},
        )
        payment.external_id = yk_payment.id
        await db.commit()

        return JSONResponse(
            {
                "ok": True,
                "payment_id": payment.id,
                "confirm_url": yk_payment.confirmation.confirmation_url if yk_payment.confirmation else None,
            }
        )
    except Exception as e:
        log.error(f"Mini App SBP error: {e}")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.get("/pay/check/{payment_id}")
async def check_payment(
    payment_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    """Check payment status and provision VPN key if succeeded."""
    tg_user = await _get_tg_user(request)
    if not tg_user:
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)

    payment = await PaymentService(db).get_by_id(payment_id)
    if not payment or payment.user_id != tg_user["id"]:
        return JSONResponse(
            {"ok": False, "error": "Payment not found"}, status_code=404
        )

    if payment.status == PaymentStatus.SUCCEEDED.value:
        from sqlalchemy import select
        from app.models.vpn_key import VpnKey
        result = await db.execute(select(VpnKey).where(VpnKey.id == payment.vpn_key_id))
        key = result.scalar_one_or_none()
        return JSONResponse({
            "ok": True, "status": "succeeded",
            "access_url": key.access_url if key else None,
            "expires_at": key.expires_at.isoformat() if key and key.expires_at is not None else None,
        })

    if payment.status == PaymentStatus.FAILED.value:
        return JSONResponse({"ok": True, "status": "failed"})

    # Still pending — check with YooKassa
    if not payment.external_id:
        return JSONResponse({"ok": True, "status": "pending"})

    try:
        from app.services.yookassa import YookassaService
        yk_payment = YookassaService().get_payment(str(payment.external_id))
        if yk_payment.status == "succeeded":
            payment.status = PaymentStatus.SUCCEEDED.value
            await db.flush()

            import json as _json
            plan_id = None
            if payment.meta:
                try:
                    meta = _json.loads(payment.meta)
                    plan_id = int(meta.get("plan_id", 0)) or None
                except Exception:
                    pass
            if plan_id is None:
                try:
                    yk_meta = yk_payment.metadata or {}
                    plan_id = int(yk_meta.get("plan_id", 0)) or None
                except Exception:
                    pass

            if plan_id is not None:
                plan = await PlanService(db).get_by_id(plan_id)
                if plan:
                    key = await VpnKeyService(db).provision(user_id=tg_user["id"], plan=plan)
                    if key:
                        payment.vpn_key_id = key.id
                    await db.commit()
                    return JSONResponse({
                        "ok": True, "status": "succeeded",
                        "access_url": key.access_url if key else None,
                        "expires_at": key.expires_at.isoformat() if key and key.expires_at is not None else None,
                    })
            await db.commit()
            return JSONResponse({"ok": True, "status": "succeeded", "access_url": None})

        elif yk_payment.status in ("canceled", "expired"):
            payment.status = PaymentStatus.FAILED.value
            await db.commit()
            return JSONResponse({"ok": True, "status": "failed"})
        else:
            return JSONResponse({"ok": True, "status": "pending"})
    except Exception as e:
        log.error(f"Mini App check payment error: {e}")
        return JSONResponse({"ok": True, "status": "pending"})


@router.get("/settings")
async def get_settings(db: AsyncSession = Depends(get_db)):
    """Get public bot settings for Mini App."""
    s = await BotSettingsService(db).get_all()
    return JSONResponse(
        {
            "ok": True,
            "lang": s.get("bot_language", "ru"),
            "about_text": s.get("about_text", ""),
            "support_url": s.get("support_url", ""),
        }
    )
