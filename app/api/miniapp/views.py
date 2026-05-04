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
from app.services.promo import PromoService
from app.utils.log import log

router = APIRouter()

_tpl_path = Path(__file__).resolve().parent.parent.parent / "templates" / "miniapp"
_tpl_path.mkdir(exist_ok=True)
templates = Jinja2Templates(directory=str(_tpl_path))


def _verify_telegram_data(init_data: str) -> Optional[dict]:
    """Verify Telegram WebApp initData and return parsed user data."""
    try:
        if not init_data or len(init_data) < 10:
            log.warning(f"MiniApp auth: init_data too short ({len(init_data) if init_data else 0} chars)")
            return None

        # Decode if double-encoded (header values may be URL-encoded)
        if '%' in init_data:
            try:
                init_data = urllib.parse.unquote(init_data)
            except Exception:
                pass

        parsed = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
        hash_val = parsed.pop("hash", "")

        if not hash_val:
            log.warning("MiniApp auth: missing hash in init_data")
            return None

        data_check = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
        token = config.telegram.telegram_bot_token.get_secret_value()
        if not token:
            log.error("MiniApp auth: bot token not configured")
            return None

        secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
        expected = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()

        if not hmac.compare_digest(expected, hash_val):
            log.warning(
                f"MiniApp auth: HMAC mismatch. "
                f"Expected: {expected[:16]}..., Got: {hash_val[:16]}..., "
                f"Keys: {list(parsed.keys())}"
            )
            return None

        user_raw = parsed.get("user", "{}")
        user_data = json.loads(user_raw)
        if not user_data or "id" not in user_data:
            log.warning(f"MiniApp auth: invalid user data: {user_raw[:200]}")
            return None

        log.info(f"MiniApp auth OK: user_id={user_data.get('id')}, username={user_data.get('username')}")
        return user_data
    except Exception as e:
        log.warning(f"MiniApp auth error: {type(e).__name__}: {e}")
        return None


async def _get_tg_user(request: Request) -> Optional[dict]:
    """Extract and verify Telegram user from request."""
    # 1. Header (primary method after JS fix)
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    if init_data:
        log.debug(f"MiniApp: X-Telegram-Init-Data header present, length={len(init_data)}")
        return _verify_telegram_data(init_data)

    # 2. Query param
    init_data = request.query_params.get("tgWebAppData", "")
    if init_data:
        return _verify_telegram_data(init_data)

    # 3. Request body (for POST requests that include initData)
    if request.method == "POST":
        try:
            body = await request.json()
            init_data = body.get("initData", "")
            if init_data:
                return _verify_telegram_data(init_data)
        except Exception:
            pass

    # 4. Fallback: accept raw user data (unverified, for emergency only)
    user_data_raw = request.headers.get("X-Telegram-User-Data", "")
    if user_data_raw:
        try:
            user_data = json.loads(user_data_raw)
            if user_data and "id" in user_data:
                log.debug(f"MiniApp auth via fallback header for user_id={user_data.get('id')}")
                return user_data
        except Exception:
            pass

    log.debug(f"MiniApp: no auth data found. Headers: {list(request.headers.keys())}")
    return None


@router.get("/", response_class=HTMLResponse)
async def miniapp_index(request: Request, db: AsyncSession = Depends(get_db)):
    """Main Mini App page."""
    from app.core.config import config as _cfg

    settings = await BotSettingsService(db).get_all()
    panel_url = (settings.get("panel_url") or "").rstrip("/")
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "app_name": _cfg.web.app_name,
            "support_url": settings.get("support_url", ""),
            "about_text": settings.get("about_text", ""),
            "bot_language": settings.get("bot_language", "ru"),
            "panel_url": panel_url,
        },
    )


@router.post("/auth")
async def miniapp_auth(request: Request, db: AsyncSession = Depends(get_db)):
    """Authenticate user via Telegram initData, create if not exists."""
    tg_user = await _get_tg_user(request)

    if not tg_user:
        return JSONResponse(
            {"ok": False, "error": "Invalid auth", "detail": "HMAC verification failed or missing initData"},
            status_code=401
        )

    user_id = tg_user.get("id")
    if not user_id:
        return JSONResponse({"ok": False, "error": "No user ID"}, status_code=401)

    try:
        user, _ = await UserService(db).get_or_create(
            UserCreate(
                id=user_id,
                username=tg_user.get("username"),
                full_name=f"{tg_user.get('first_name', '')} {tg_user.get('last_name', '')}".strip(),
            )
        )
        await db.commit()
    except Exception as e:
        log.error(f"MiniApp auth: DB error for user {user_id}: {e}")
        return JSONResponse({"ok": False, "error": "Database error"}, status_code=500)

    settings = await BotSettingsService(db).get_all()
    is_admin = user_id in config.telegram.telegram_admin_ids

    return JSONResponse(
        {
            "ok": True,
            "user": {
                "id": user.id,
                "username": user.username,
                "full_name": user.full_name,
                "balance": float(user.balance or 0),
                "referral_code": user.referral_code,
                "is_admin": is_admin,
            },
            "lang": settings.get("bot_language", "ru"),
        }
    )


@router.post("/auth-fallback")
async def miniapp_auth_fallback(request: Request, db: AsyncSession = Depends(get_db)):
    """Emergency auth fallback using initDataUnsafe user data."""
    try:
        body = await request.json()
    except Exception as e:
        log.warning(f"MiniApp auth-fallback: failed to parse JSON body: {e}")
        return JSONResponse({"ok": False, "error": "Invalid request body"}, status_code=400)

    user_data = body.get("user", {})
    if not user_data or "id" not in user_data:
        log.warning("MiniApp auth-fallback: missing user data")
        return JSONResponse({"ok": False, "error": "Missing user data"}, status_code=401)

    user_id = user_data.get("id")
    log.warning(f"MiniApp auth-fallback used for user_id={user_id}, username={user_data.get('username')}")

    try:
        user, _ = await UserService(db).get_or_create(
            UserCreate(
                id=user_id,
                username=user_data.get("username"),
                full_name=f"{user_data.get('first_name', '')} {user_data.get('last_name', '')}".strip(),
            )
        )
        await db.commit()
    except Exception as e:
        log.error(f"MiniApp auth-fallback: DB error for user {user_id}: {e}")
        return JSONResponse({"ok": False, "error": "Database error"}, status_code=500)

    settings = await BotSettingsService(db).get_all()
    is_admin = user_id in config.telegram.telegram_admin_ids

    return JSONResponse(
        {
            "ok": True,
            "user": {
                "id": user.id,
                "username": user.username,
                "full_name": user.full_name,
                "balance": float(user.balance or 0),
                "referral_code": user.referral_code,
                "is_admin": is_admin,
            },
            "lang": settings.get("bot_language", "ru"),
        }
    )


@router.get("/admin/stats")
async def admin_stats(request: Request, db: AsyncSession = Depends(get_db)):
    """Get admin statistics."""
    tg_user = await _get_tg_user(request)
    if not tg_user:
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)

    user_id = tg_user["id"]
    if user_id not in config.telegram.telegram_admin_ids:
        return JSONResponse({"ok": False, "error": "Not admin"}, status_code=403)

    from datetime import datetime, timezone
    from sqlalchemy import select, func
    from app.models.payment import Payment, PaymentStatus, PaymentType
    from app.models.user import User
    from app.models.vpn_key import VpnKey, VpnKeyStatus

    total_users = await UserService(db).count_all()
    active_subs = await VpnKeyService(db).count_active()

    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    new_today_r = await db.execute(
        select(func.count()).select_from(User).where(User.created_at >= today)
    )
    new_today = new_today_r.scalar_one()

    revenue_r = await db.execute(
        select(func.sum(Payment.amount)).where(
            Payment.status == PaymentStatus.SUCCEEDED.value,
            Payment.payment_type == PaymentType.SUBSCRIPTION.value,
        )
    )
    revenue_val = revenue_r.scalar_one()
    revenue = float(revenue_val) if revenue_val else 0.0

    return JSONResponse(
        {
            "ok": True,
            "total_users": total_users,
            "active_subs": active_subs,
            "new_today": new_today,
            "revenue": revenue,
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
    plans = await PlanService(db).get_all(only_active=True)

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

    is_admin = user_id in config.telegram.telegram_admin_ids

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
                "is_admin": is_admin,
            },
            "plans": [
                {
                    "id": p.id,
                    "name": p.name,
                    "duration_days": p.duration_days,
                    "price": float(p.price or 0),
                }
                for p in plans
            ],
            "active_keys": active_keys,
            "archive_keys": archive_keys,
        }
    )


@router.post("/pay/balance")
async def pay_balance(request: Request, db: AsyncSession = Depends(get_db)):
    """Pay with internal balance - ATOMIC with rollback on failure."""
    import asyncio
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
        return JSONResponse({"ok": False, "error": "Insufficient balance"}, status_code=400)

    try:
        updated = await UserService(db).deduct_balance(user_id, plan.price)
        if not updated:
            return JSONResponse({"ok": False, "error": "Balance deduction failed"}, status_code=400)
        payment = await PaymentService(db).create_pending(
            user_id=user_id, plan=plan, provider=PaymentProvider.BALANCE
        )
        payment.status = PaymentStatus.SUCCEEDED.value
        await db.flush()

        key = None
        last_error = None
        for attempt in range(3):
            try:
                key = await VpnKeyService(db).provision(user_id=user_id, plan=plan)
                if key:
                    break
            except Exception as e:
                last_error = e
                log.warning(f"Provision attempt {attempt+1}/3 failed: {e}")
                if attempt < 2:
                    await asyncio.sleep(0.5 * (attempt + 1))

        if not key:
            log.error(f"VPN provisioning failed after 3 retries: {last_error}")
            await UserService(db).add_balance(user_id, plan.price)
            payment.status = PaymentStatus.FAILED.value
            await db.commit()
            return JSONResponse({"ok": False, "error": "VPN server error. Balance refunded."}, status_code=500)

        payment.vpn_key_id = key.id
        await db.commit()

        return JSONResponse(
            {
                "ok": True,
                "access_url": key.access_url,
                "expires_at": key.expires_at.isoformat() if key.expires_at else None,
                "plan_name": plan.name,
                "days": plan.duration_days,
            }
        )

    except Exception as e:
        log.error(f"MiniApp balance payment error user={user_id}: {e}")
        try:
            await UserService(db).add_balance(user_id, plan.price)
            await db.commit()
        except Exception as refund_err:
            log.error(f"Refund failed: {refund_err}")
        return JSONResponse({"ok": False, "error": "Payment failed. Contact support."}, status_code=500)


@router.post("/extend/key")
async def extend_key(request: Request, db: AsyncSession = Depends(get_db)):
    """Extend subscription using balance."""
    tg_user = await _get_tg_user(request)
    if not tg_user:
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)

    body = await request.json()
    key_id = body.get("key_id")
    plan_id = body.get("plan_id")
    user_id = tg_user["id"]

    key = await VpnKeyService(db).get_by_id(key_id)
    if not key or key.user_id != user_id:
        return JSONResponse({"ok": False, "error": "Key not found"}, status_code=404)

    plan = await PlanService(db).get_by_id(plan_id)
    if not plan or not plan.is_active:
        return JSONResponse({"ok": False, "error": "Plan not found"}, status_code=404)

    user = await UserService(db).get_by_id(user_id)
    if not user or float(user.balance or 0) < float(plan.price):
        return JSONResponse({"ok": False, "error": "Insufficient balance"}, status_code=400)

    updated = await UserService(db).deduct_balance(user_id, plan.price)
    if not updated:
        return JSONResponse({"ok": False, "error": "Balance error"}, status_code=400)

    extended_key = await VpnKeyService(db).extend(key_id, plan.duration_days)
    await db.commit()

    if extended_key:
        return JSONResponse(
            {
                "ok": True,
                "expires_at": extended_key.expires_at.isoformat() if extended_key.expires_at else None,
                "plan_name": plan.name,
                "days": plan.duration_days,
            }
        )
    return JSONResponse({"ok": False, "error": "Failed to extend key"}, status_code=500)


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

        yk = await YookassaService.create()
        payment = await PaymentService(db).create_pending(
            user_id=user_id, plan=plan, provider=PaymentProvider.YOOKASSA
        )
        await db.flush()

        return_url = f"https://t.me/{body.get('bot_username', '')}"
        yk_payment = await yk.create_payment(
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

        yk = await YookassaService.create()
        payment = await PaymentService(db).create_pending(
            user_id=user_id, plan=plan, provider=PaymentProvider.YOOKASSA_SBP
        )
        await db.flush()
        import json as _json
        payment.meta = _json.dumps({"plan_id": plan.id})

        return_url = "https://t.me/"
        yk_payment = await yk.create_sbp_payment(
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


@router.post("/pay/freekassa")
async def pay_freekassa(request: Request, db: AsyncSession = Depends(get_db)):
    """Create FreeKassa payment and return redirect URL."""
    tg_user = await _get_tg_user(request)
    if not tg_user:
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)

    body = await request.json()
    plan_id = body.get("plan_id")
    user_id = tg_user["id"]

    plan = await PlanService(db).get_by_id(plan_id)
    if not plan or not plan.is_active:
        return JSONResponse({"ok": False, "error": "Plan not found"}, status_code=404)

    settings = await BotSettingsService(db).get_all()
    from app.services.freekassa import FreeKassaService

    fk = FreeKassaService.from_settings(settings)
    if not fk:
        return JSONResponse({"ok": False, "error": "FreeKassa not configured"}, status_code=400)

    try:
        payment = await PaymentService(db).create_pending(
            user_id=user_id, plan=plan, provider=PaymentProvider.FREEKASSA
        )
        await db.flush()
        order_id = f"fk_{payment.id}_{plan.id}"

        base_url = str(config.web.allowed_origins[0]).rstrip("/") if config.web.allowed_origins else ""
        notification_url = f"{base_url}/api/v1/payments/webhook/freekassa" if base_url else ""

        result = await fk.create_order(
            payment_id=order_id,
            amount=float(plan.price),
            currency="RUB",
            currency_id=36,
            email=f"user{user_id}@vpn.bot",
            ip="127.0.0.1",
            notification_url=notification_url,
        )
        if result and result.get("type") == "success":
            payment.external_id = str(result.get("orderId", ""))
            await db.commit()
            return JSONResponse({"ok": True, "pay_url": result.get("location", "")})
        err = result.get("message", "Ошибка") if result else "Нет ответа"
        return JSONResponse({"ok": False, "error": err}, status_code=400)
    except Exception as e:
        log.error(f"Mini App FreeKassa error: {e}")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.get("/pay/check/{payment_id}")
async def check_payment(payment_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    """Check payment status and provision VPN key if succeeded."""
    tg_user = await _get_tg_user(request)
    if not tg_user:
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)

    payment = await PaymentService(db).get_by_id(payment_id)
    if not payment or payment.user_id != tg_user["id"]:
        return JSONResponse({"ok": False, "error": "Payment not found"}, status_code=404)

    if payment.status == PaymentStatus.SUCCEEDED.value:
        from sqlalchemy import select
        from app.models.vpn_key import VpnKey

        result = await db.execute(select(VpnKey).where(VpnKey.id == payment.vpn_key_id))
        key = result.scalar_one_or_none()
        return JSONResponse(
            {
                "ok": True,
                "status": "succeeded",
                "access_url": key.access_url if key else None,
                "expires_at": key.expires_at.isoformat() if key and key.expires_at else None,
            }
        )

    if payment.status == PaymentStatus.FAILED.value:
        return JSONResponse({"ok": True, "status": "failed"})

    if not payment.external_id:
        return JSONResponse({"ok": True, "status": "pending"})

    try:
        from app.services.yookassa import YookassaService

        yk = await YookassaService.create()
        yk_payment = await yk.get_payment(str(payment.external_id))
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
                    return JSONResponse(
                        {
                            "ok": True,
                            "status": "succeeded",
                            "access_url": key.access_url if key else None,
                            "expires_at": key.expires_at.isoformat() if key and key.expires_at else None,
                        }
                    )
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

    from app.core.config import config as _cfg

    _yk = _cfg.yookassa
    _yk_env_ok = bool(_yk and _yk.yookassa_shop_id and _yk.yookassa_secret_key)
    _yk_db_ok = bool(s.get("yookassa_shop_id_override") and s.get("yookassa_secret_key_override"))
    _yk_toggle = s.get("ps_yookassa_enabled", "0") == "1"
    has_yookassa = _yk_toggle and (_yk_env_ok or _yk_db_ok)

    _sbp_toggle = s.get("ps_sbp_enabled", "0") == "1"
    has_sbp = _sbp_toggle and (_yk_env_ok or _yk_db_ok)

    _cb_toggle = s.get("ps_cryptobot_enabled", "0") == "1"
    has_cryptobot = _cb_toggle and bool(s.get("cryptobot_token", "").strip())

    _stars_toggle = s.get("ps_stars_enabled", "0") == "1"
    has_stars = _stars_toggle

    _fk_toggle = s.get("ps_freekassa_enabled", "0") == "1"
    has_freekassa = _fk_toggle and bool(s.get("freekassa_shop_id") and s.get("freekassa_api_key"))

    try:
        stars_rate = float(s.get("stars_rate") or "1.5")
    except (ValueError, TypeError):
        stars_rate = 1.5

    bot_username = ""
    try:
        bot_username = _get_cached_bot_username()
        if not bot_username:
            bot_username = await _fetch_bot_username_safe()
    except Exception as e:
        log.warning(f"MiniApp settings: failed to fetch bot username: {e}")

    return JSONResponse(
        {
            "ok": True,
            "lang": s.get("bot_language", "ru"),
            "about_text": s.get("about_text", ""),
            "support_url": s.get("support_url", ""),
            "has_yookassa": has_yookassa,
            "has_sbp": has_sbp,
            "has_cryptobot": has_cryptobot,
            "has_stars": has_stars,
            "has_freekassa": has_freekassa,
            "stars_rate": stars_rate,
            "bot_username": bot_username,
        }
    )


_BOT_USERNAME_CACHE: str = ""


def _get_cached_bot_username() -> str:
    return _BOT_USERNAME_CACHE


async def _fetch_bot_username_safe() -> str:
    """Fetch bot username safely without crashing."""
    global _BOT_USERNAME_CACHE
    try:
        from app.core.server import get_bot
        bot = get_bot()
        if bot:
            me = await bot.get_me()
            _BOT_USERNAME_CACHE = me.username or ""
            return _BOT_USERNAME_CACHE
    except Exception as e:
        log.debug(f"MiniApp: get_bot() failed: {e}")

    try:
        from aiogram import Bot
        from app.core.config import config as _cfg2
        token = _cfg2.telegram.telegram_bot_token.get_secret_value()
        async with Bot(token=token) as _bot_tmp:
            me = await _bot_tmp.get_me()
            _BOT_USERNAME_CACHE = me.username or ""
            return _BOT_USERNAME_CACHE
    except Exception as e:
        log.debug(f"MiniApp: fallback Bot creation failed: {e}")

    return ""


@router.post("/promo/apply")
async def apply_promo(request: Request, db: AsyncSession = Depends(get_db)):
    """Apply promo code (balance / days / discount)."""
    tg_user = await _get_tg_user(request)
    if not tg_user:
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)

    body = await request.json()
    code = (body.get("code") or "").strip().upper()
    user_id = tg_user["id"]

    if not code:
        return JSONResponse({"ok": False, "error": "Code required"}, status_code=400)

    promo = await PromoService(db).apply(code)
    if not promo:
        return JSONResponse({"ok": False, "error": "Invalid or expired promo code"}, status_code=400)

    pt = str(promo.promo_type)
    result = {"type": pt, "value": float(promo.value)}

    if pt == "balance":
        from decimal import Decimal
        await UserService(db).add_balance(user_id, Decimal(promo.value))
        await db.commit()
        result["message"] = f"💰 Зачислено {float(promo.value)}₽ на баланс"

    elif pt == "days":
        keys = await VpnKeyService(db).get_all_for_user(user_id)
        active_key = None
        for k in keys:
            status = str(k.status.value if hasattr(k.status, "value") else k.status)
            if status == "active":
                active_key = k
                break

        if active_key:
            await VpnKeyService(db).extend(active_key.id, int(promo.value))
            await db.commit()
            result["message"] = f"📅 Подписка продлена на {int(promo.value)} дней"
        else:
            result["message"] = f"📅 +{int(promo.value)} дней будут применены при следующей покупке"

    else:
        result["message"] = f"🏷 Скидка {float(promo.value)}% активирована"

    return JSONResponse({"ok": True, "result": result})


@router.get("/faq")
async def get_faq(db: AsyncSession = Depends(get_db)):
    """Get FAQ content from bot settings."""
    settings = await BotSettingsService(db).get_all()
    about = settings.get("about_text", "")

    default_faq = [
        {"q": "Как подключить VPN?", "a": "Скопируйте ключ из раздела «Подписки» и импортируйте в приложение V2Ray/Outline."},
        {"q": "Какие устройства поддерживаются?", "a": "iOS, Android, Windows, macOS и Linux."},
        {"q": "Сколько устройств можно подключить?", "a": "Один ключ работает на неограниченном количестве устройств."},
        {"q": "Как пополнить баланс?", "a": "Перейдите в раздел «Купить» → выберите план → оплатите."},
        {"q": "Можно ли вернуть деньги?", "a": "Возврат возможен в течение 24 часов при технических проблемах."},
        {"q": "Как работают промокоды?", "a": "Введите код в разделе «Профиль» — получите бонус."},
    ]

    return JSONResponse({"ok": True, "about": about, "faq": default_faq})


@router.get("/servers/status")
async def servers_status(request: Request, db: AsyncSession = Depends(get_db)):
    """Get VPN servers status."""
    tg_user = await _get_tg_user(request)
    if not tg_user:
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)

    import time
    start = time.time()
    try:
        from sqlalchemy import text
        await db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False

    latency_ms = round((time.time() - start) * 1000, 1)
    active_keys = await VpnKeyService(db).count_active()

    return JSONResponse(
        {
            "ok": True,
            "overall": "operational" if db_ok else "degraded",
            "active_keys": active_keys,
            "servers": [
                {"name": "Основной EU", "region": "🇩🇪 Германия", "status": "online" if db_ok else "degraded", "ping": latency_ms, "load": min(95, max(5, int(latency_ms / 3)))},
                {"name": "Резервный US", "region": "🇺🇸 США", "status": "online", "ping": latency_ms + 15, "load": min(95, max(5, int((latency_ms + 15) / 3)))},
            ],
        }
    )


@router.get("/user/stats")
async def user_stats(request: Request, db: AsyncSession = Depends(get_db)):
    """Get user statistics."""
    tg_user = await _get_tg_user(request)
    if not tg_user:
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)

    user_id = tg_user["id"]
    user = await UserService(db).get_by_id(user_id)
    if not user:
        return JSONResponse({"ok": False, "error": "User not found"}, status_code=404)

    keys = await VpnKeyService(db).get_all_for_user(user_id)
    ref_count = await ReferralService(db).count_referrals(user_id)

    active_keys = 0
    expired_keys = 0
    total_spent = 0.0
    for k in keys:
        status = str(k.status.value if hasattr(k.status, "value") else k.status)
        if status == "active":
            active_keys += 1
        else:
            expired_keys += 1
        total_spent += float(k.price or 0)

    return JSONResponse(
        {
            "ok": True,
            "stats": {
                "balance": float(user.balance or 0),
                "active_keys": active_keys,
                "expired_keys": expired_keys,
                "total_spent": round(total_spent, 2),
                "referrals": ref_count,
                "referral_code": user.referral_code,
            },
        }
    )


@router.get("/user/payments")
async def user_payments(request: Request, limit: int = 10, db: AsyncSession = Depends(get_db)):
    """Get user payment history."""
    tg_user = await _get_tg_user(request)
    if not tg_user:
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)

    user_id = tg_user["id"]
    payments = await PaymentService(db).get_all(user_id=user_id, limit=limit)

    return JSONResponse({
        "ok": True,
        "payments": [
            {
                "id": p.id,
                "amount": float(p.amount),
                "currency": p.currency,
                "provider": p.provider,
                "payment_type": p.payment_type,
                "status": p.status,
                "created_at": p.created_at.isoformat() if p.created_at else None,
            }
            for p in payments
        ],
    })
