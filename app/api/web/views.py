import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db
from app.core.config import config
from app.models.payment import PaymentStatus, PaymentType
from app.models.vpn_key import VpnKeyStatus
from app.services.bot_settings import BotSettingsService
from app.services.payment import PaymentService
from app.services.plan import PlanService
from app.services.telegram_notify import TelegramNotifyService
from app.schemas.user import UserCreate
from app.services.user import UserService
from app.services.vpn_key import VpnKeyService
from app.utils.log import log
from app.utils.security import create_access_token, decode_access_token_full

router = APIRouter()

_tpl_path = Path(__file__).resolve().parent.parent.parent / "templates"
templates = Jinja2Templates(directory=str(_tpl_path))

WEB_SESSION_COOKIE = "vpn_web_session"


def _verify_telegram_auth(data: dict) -> bool:
    """Verify Telegram Login Widget data using bot token hash."""
    auth_hash = data.pop("hash", "")
    check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()) if v is not None)
    secret_key = hashlib.sha256(config.telegram.telegram_bot_token.get_secret_value().encode()).digest()
    computed_hash = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
    return computed_hash == auth_hash


def _get_user_info(request: Request) -> dict | None:
    """Extract user info from web session cookie."""
    token = request.cookies.get(WEB_SESSION_COOKIE)
    if not token:
        return None
    return decode_access_token_full(token)


def _require_user_auth(request: Request) -> dict:
    """Enforce user authentication. Returns {"sub": str, ...}."""
    info = _get_user_info(request)
    if info is None:
        raise _redirect("/login")
    return info


def _redirect(url: str):
    from fastapi import HTTPException
    raise HTTPException(status_code=302, headers={"Location": url})


async def _web_base_ctx(request: Request, db: AsyncSession, active: str = "") -> dict:
    user_info = _get_user_info(request)
    user = None
    if user_info:
        user = await UserService(db).get_by_id(int(user_info["sub"]))
    return {
        "request": request,
        "active": active,
        "user": user,
        "app_name": config.web.app_name,
        "app_version": config.web.app_version,
        "bot_username": None,
    }


# ── Landing Page ─────────────────────────────────────────────────────────────


@router.get("/", response_class=HTMLResponse)
async def landing_page(request: Request, db: AsyncSession = Depends(get_db)):
    ctx = await _web_base_ctx(request, db, "home")
    ctx["plans"] = await PlanService(db).get_all(only_active=True)
    ctx["features"] = [
        {"icon": "bi-shield-check", "title": "Безопасность", "desc": "Шифрование трафика и защита данных"},
        {"icon": "bi-lightning-charge", "title": "Скорость", "desc": "Высокоскоростные серверы по всему миру"},
        {"icon": "bi-globe", "title": "Доступ", "desc": "Обход блокировок и гео-ограничений"},
        {"icon": "bi-phone", "title": "Все устройства", "desc": "Поддержка iOS, Android, Windows, macOS"},
        {"icon": "bi-headset", "title": "Поддержка", "desc": "Круглосуточная техподдержка"},
        {"icon": "bi-credit-card", "title": "Оплата", "desc": "Удобная оплата картой, криптой, Stars"},
    ]
    return templates.TemplateResponse("web/landing.html", ctx)


# ── Login ────────────────────────────────────────────────────────────────────


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, db: AsyncSession = Depends(get_db)):
    # Already logged in → redirect to dashboard
    if _get_user_info(request):
        return RedirectResponse(url="/dashboard", status_code=302)
    
    ctx = await _web_base_ctx(request, db, "login")
    ctx["bot_username"] = None
    # Try to get bot username for login widget
    try:
        bot_info = await TelegramNotifyService().get_bot_info()
        if bot_info:
            ctx["bot_username"] = bot_info.get("username", "")
    except Exception:
        pass
    return templates.TemplateResponse("web/login.html", ctx)


@router.post("/auth/telegram")
async def telegram_auth_callback(
    request: Request,
    id: int = Form(...),
    first_name: str = Form(...),
    last_name: Optional[str] = Form(None),
    username: Optional[str] = Form(None),
    photo_url: Optional[str] = Form(None),
    auth_date: int = Form(...),
    hash: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    """Handle Telegram Login Widget callback."""
    # Verify auth data
    auth_data = {
        "id": id,
        "first_name": first_name,
        "last_name": last_name,
        "username": username,
        "photo_url": photo_url,
        "auth_date": auth_date,
        "hash": hash,
    }
    
    # Check auth_date is recent (within 1 hour)
    now = int(time.time())
    if now - auth_date > 3600:
        return JSONResponse({"ok": False, "error": "Auth expired"}, status_code=400)
    
    # Verify hash
    check_data = {k: v for k, v in auth_data.items() if k != "hash" and v is not None}
    if not _verify_telegram_auth(check_data.copy()):
        return JSONResponse({"ok": False, "error": "Invalid auth data"}, status_code=403)
    
    # Get or create user
    user = await UserService(db).get_by_id(id)
    if not user:
        full_name = f"{first_name} {last_name or ''}".strip()
        user = await UserService(db).create(
            UserCreate(id=id, username=username, full_name=full_name)
        )
        await db.commit()
        log.info(f"New user registered via web: {id} ({full_name})")
    else:
        # Update user info
        if username and user.username != username:
            user.username = username
        full_name = f"{first_name} {last_name or ''}".strip()
        if full_name and user.full_name != full_name:
            user.full_name = full_name
        await db.commit()
    
    # Create JWT token
    token = create_access_token(subject=str(id), role="user")
    
    # Set cookie and redirect
    resp = RedirectResponse(url="/dashboard", status_code=302)
    resp.set_cookie(
        WEB_SESSION_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        max_age=86400 * 30,  # 30 days
    )
    return resp


@router.get("/logout")
async def logout():
    resp = RedirectResponse(url="/", status_code=302)
    resp.delete_cookie(WEB_SESSION_COOKIE)
    return resp


# ── Dashboard ────────────────────────────────────────────────────────────────


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request, db: AsyncSession = Depends(get_db)):
    user_info = _require_user_auth(request)
    ctx = await _web_base_ctx(request, db, "dashboard")
    
    user_id = int(user_info["sub"])
    user = await UserService(db).get_by_id(user_id)
    if not user:
        resp = RedirectResponse(url="/logout", status_code=302)
        resp.delete_cookie(WEB_SESSION_COOKIE)
        return resp
    
    # Get user's VPN keys
    keys = await VpnKeyService(db).get_all_for_user(user_id)
    
    # Get active plans
    plans = await PlanService(db).get_all(only_active=True)
    
    # Get payment history
    payments = await PaymentService(db).get_all(user_id=user_id, limit=10)
    
    ctx["user"] = user
    ctx["keys"] = keys
    ctx["plans"] = plans
    ctx["payments"] = payments
    
    return templates.TemplateResponse("web/dashboard.html", ctx)


# ── API Endpoints ────────────────────────────────────────────────────────────


@router.get("/api/plans")
async def api_plans(db: AsyncSession = Depends(get_db)):
    """Get all active plans (public API)."""
    plans = await PlanService(db).get_all(only_active=True)
    return {
        "plans": [
            {
                "id": p.id,
                "name": p.name,
                "slug": p.slug,
                "description": p.description,
                "duration_days": p.duration_days,
                "price": float(p.price),
                "currency": p.currency,
            }
            for p in plans
        ]
    }


@router.post("/api/create-payment")
async def api_create_payment(
    request: Request,
    plan_id: int = Form(...),
    provider: str = Form("yookassa"),
    db: AsyncSession = Depends(get_db),
):
    """Create a payment for a plan."""
    user_info = _get_user_info(request)
    if not user_info:
        return JSONResponse({"ok": False, "error": "Not authenticated"}, status_code=401)
    
    user_id = int(user_info["sub"])
    plan = await PlanService(db).get_by_id(plan_id)
    if not plan or not plan.is_active:
        return JSONResponse({"ok": False, "error": "Plan not found"}, status_code=404)
    
    # Create payment record
    from app.models.payment import PaymentProvider
    payment = await PaymentService(db).create_pending(
        user_id=user_id,
        plan=plan,
        provider=PaymentProvider(provider),
        currency=plan.currency,
    )
    await db.commit()
    
    # Generate payment URL based on provider
    payment_url = None
    if provider == "yookassa":
        try:
            from app.services.yookassa import YookassaService
            from fastapi.concurrency import run_in_threadpool
            yk = await YookassaService.create()
            result = await run_in_threadpool(
                yk.create_payment,
                amount=plan.price,
                description=f"Подписка {plan.name} ({plan.duration_days} дней)",
                return_url=str(request.base_url).rstrip("/") + "/dashboard",
                metadata={"payment_id": str(payment.id), "user_id": str(user_id)},
            )
            # Extract confirmation URL from YooKassa response
            confirmation = getattr(result, "confirmation", None)
            if confirmation:
                payment_url = getattr(confirmation, "confirmation_url", None)
            if not payment_url:
                # Fallback: try dict-like access
                try:
                    payment_url = result["confirmation"]["confirmation_url"]
                except (TypeError, KeyError):
                    payment_url = None
            if not payment_url:
                raise ValueError("No confirmation URL in YooKassa response")
        except Exception as e:
            log.error(f"YooKassa payment creation failed: {e}")
            return JSONResponse({"ok": False, "error": "Payment provider error"}, status_code=500)
    elif provider == "cryptobot":
        try:
            from app.services.cryptobot import CryptoBotService
            token = await BotSettingsService(db).get("cryptobot_token")
            if not token:
                return JSONResponse({"ok": False, "error": "CryptoBot not configured"}, status_code=400)
            cb = CryptoBotService(token)
            invoice = await cb.create_invoice(
                amount=float(plan.price),
                description=f"Подписка {plan.name}",
                payload=str(payment.id),
            )
            if not invoice:
                raise ValueError("No invoice created")
            payment_url = invoice.get("pay_url") or invoice.get("bot_invoice_url")
            if not payment_url:
                raise ValueError("No payment URL in CryptoBot response")
        except Exception as e:
            log.error(f"CryptoBot payment creation failed: {e}")
            return JSONResponse({"ok": False, "error": "Payment provider error"}, status_code=500)
    
    return {
        "ok": True,
        "payment_id": payment.id,
        "payment_url": payment_url,
        "amount": float(plan.price),
        "currency": plan.currency,
    }


@router.get("/api/my-keys")
async def api_my_keys(request: Request, db: AsyncSession = Depends(get_db)):
    """Get current user's VPN keys."""
    user_info = _get_user_info(request)
    if not user_info:
        return JSONResponse({"ok": False, "error": "Not authenticated"}, status_code=401)
    
    user_id = int(user_info["sub"])
    keys = await VpnKeyService(db).get_all_for_user(user_id)
    
    return {
        "keys": [
            {
                "id": k.id,
                "name": k.name,
                "access_url": k.access_url,
                "status": k.status,
                "expires_at": k.expires_at.isoformat() if k.expires_at else None,
                "created_at": k.created_at.isoformat() if k.created_at else None,
            }
            for k in keys
        ]
    }


@router.get("/api/payment-status/{payment_id}")
async def api_payment_status(
    payment_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Check payment status."""
    user_info = _get_user_info(request)
    if not user_info:
        return JSONResponse({"ok": False, "error": "Not authenticated"}, status_code=401)
    
    payment = await PaymentService(db).get_by_id(payment_id)
    if not payment or payment.user_id != int(user_info["sub"]):
        return JSONResponse({"ok": False, "error": "Payment not found"}, status_code=404)
    
    return {
        "ok": True,
        "status": payment.status,
        "amount": float(payment.amount),
        "currency": payment.currency,
    }
