import gzip
import io
import json
import subprocess
import tempfile
from decimal import Decimal
from datetime import datetime
from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from pathlib import Path

from app.api.dependencies import get_db
from app.core.config import config
from app.utils.log import log
from app.models.payment import PaymentStatus
from app.models.support import TicketStatus, TicketPriority
from app.services.broadcast import BroadcastService
from app.services.payment import PaymentService
from app.services.plan import PlanService
from app.services.promo import PromoService
from app.services.referral import ReferralService
from app.services.vpn_key import VpnKeyService
from app.services.support import SupportService
from app.services.telegram_notify import TelegramNotifyService
from app.services.user import UserService
from app.services.bot_settings import BotSettingsService
from app.schemas.user import UserDetail, UserRead
from app.utils.security import create_access_token

router = APIRouter()

_tpl_path = Path(__file__).resolve().parent.parent.parent / "templates"
templates = Jinja2Templates(directory=str(_tpl_path))

SESSION_COOKIE = "vpn_session"


def _toast(resp: Response, message: str, kind: str = "success") -> None:
    """Unicode-safe toast via HX-Trigger JSON header."""
    resp.headers["HX-Trigger"] = json.dumps({"showToast": {"msg": message, "type": kind}})


def _check_session(request: Request) -> bool:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return False
    from app.utils.security import decode_access_token
    return decode_access_token(token) is not None


def _require_auth(request: Request) -> None:
    if not _check_session(request):
        is_htmx = request.headers.get("HX-Request") == "true"
        # JSON API requests (Accept: application/json or /api/ path) → 401
        is_api = "/api/" in str(request.url.path)
        is_json = "application/json" in request.headers.get("accept", "")
        if is_api or is_json:
            from fastapi import HTTPException
            raise HTTPException(status_code=401, detail="Not authenticated")
        if is_htmx:
            from fastapi import HTTPException
            raise HTTPException(
                status_code=200,
                headers={"HX-Redirect": "/panel/login"},
            )
        raise _redirect("/panel/login")


def _redirect(url: str):
    from fastapi import HTTPException
    raise HTTPException(status_code=302, headers={"Location": url})


async def _base_ctx(request: Request, db: AsyncSession, active: str) -> dict:
    open_tickets = await SupportService(db).count_open()
    pending_payments = await PaymentService(db).count_by_status(PaymentStatus.PENDING)
    return {
        "request": request,
        "active": active,
        "open_tickets": open_tickets,
        "pending_payments": pending_payments,
        "bot_username": None,
        "app_name": config.web.app_name,
        "app_version": config.web.app_version,
    }

# ── Mini App auto-login ───────────────────────────────────────────────────────
# One-time tokens stored in memory: {token: expiry_timestamp}
import time as _time
_miniapp_tokens: dict[str, float] = {}


@router.get("/miniapp-token")
async def get_miniapp_token(request: Request):
    """Генерирует одноразовый токен для авто-входа из Mini App. Только для авторизованных."""
    _check_session(request) or _redirect("/panel/login")
    import secrets as _secrets
    token = _secrets.token_urlsafe(32)
    _miniapp_tokens[token] = _time.time() + 300  # 5 минут
    return {"token": token}


@router.get("/miniapp-login")
async def miniapp_login(request: Request, token: str = ""):
    """Авто-вход по одноразовому токену из Mini App."""
    now = _time.time()
    # Cleanup expired
    expired = [k for k, v in _miniapp_tokens.items() if v < now]
    for k in expired:
        del _miniapp_tokens[k]

    if not token or token not in _miniapp_tokens or _miniapp_tokens[token] < now:
        return RedirectResponse(url="/panel/login", status_code=302)

    del _miniapp_tokens[token]  # одноразовый
    session_token = create_access_token(subject=config.web.web_superadmin_username)
    resp = RedirectResponse(url="/panel/", status_code=302)
    resp.set_cookie(SESSION_COOKIE, session_token, httponly=True, samesite="none", secure=True, max_age=3600)
    return resp


# ── Auth ──────────────────────────────────────────────────────────────────────

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {
        "request": request,
        "error": None,
        "app_name": config.web.app_name,
        "app_version": config.web.app_version,
    })


@router.post("/login")
async def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    if username != config.web.web_superadmin_username or \
       password != config.web.web_superadmin_password.get_secret_value():
        return templates.TemplateResponse("login.html", {
            "request": request,
            "error": "Неверный логин или пароль",
            "app_name": config.web.app_name,
            "app_version": config.web.app_version,
        })
    token = create_access_token(subject=username)
    resp = RedirectResponse(url="/panel/", status_code=302)
    resp.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax", max_age=86400)
    return resp


@router.get("/logout")
async def logout():
    resp = RedirectResponse(url="/panel/login", status_code=302)
    resp.delete_cookie(SESSION_COOKIE)
    return resp


# ── Dashboard ─────────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    _require_auth(request)
    ctx = await _base_ctx(request, db, "dashboard")

    from datetime import datetime, timezone, timedelta
    from sqlalchemy import select, func
    from app.models.user import User
    from app.models.payment import Payment, PaymentStatus
    from app.models.vpn_key import VpnKey, VpnKeyStatus

    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    # New users today
    new_today_r = await db.execute(
        select(func.count()).select_from(User).where(User.created_at >= today_start)
    )
    new_today = new_today_r.scalar_one()

    # Revenue today
    rev_today_r = await db.execute(
        select(func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.status == PaymentStatus.SUCCEEDED.value,
            Payment.created_at >= today_start,
        )
    )
    rev_today = float(rev_today_r.scalar_one())

    # Expired keys count
    expired_r = await db.execute(
        select(func.count()).select_from(VpnKey).where(VpnKey.status == VpnKeyStatus.EXPIRED.value)
    )
    expired_count = expired_r.scalar_one()

    # Revenue last 7 days
    rev_week = []
    for i in range(6, -1, -1):
        day_start = today_start - timedelta(days=i)
        day_end = day_start + timedelta(days=1)
        r = await db.execute(
            select(func.coalesce(func.sum(Payment.amount), 0)).where(
                Payment.status == PaymentStatus.SUCCEEDED.value,
                Payment.created_at >= day_start,
                Payment.created_at < day_end,
            )
        )
        rev_week.append(float(r.scalar_one()))

    ctx["stats"] = {
        "total_users": await UserService(db).count_all(),
        "active_subscriptions": await VpnKeyService(db).count_active(),
        "total_revenue": await PaymentService(db).total_revenue(),
        "open_tickets": await SupportService(db).count_open(),
        "new_users_today": new_today,
        "revenue_today": rev_today,
        "expired_keys": expired_count,
        "pending_payments": await PaymentService(db).count_by_status(PaymentStatus.PENDING),
    }
    ctx["rev_week"] = rev_week
    ctx["recent_users"] = await UserService(db).get_all(limit=8)
    ctx["recent_payments"] = await PaymentService(db).get_all(limit=8)

    from app.services.pasarguard.pasarguard import get_vpn_panel
    try:
        marzban_stats = await get_vpn_panel().get_system_stats()
        ctx["marzban_stats"] = marzban_stats
    except Exception:
        ctx["marzban_stats"] = None

    return templates.TemplateResponse("dashboard.html", ctx)


# ── SPA API endpoints ─────────────────────────────────────────────────────────

@router.get("/api/dashboard")
async def spa_dashboard_api(request: Request, db: AsyncSession = Depends(get_db)):
    """JSON API for SPA dashboard."""
    from datetime import datetime, timezone, timedelta
    from sqlalchemy import select, func
    from app.models.user import User
    from app.models.payment import Payment, PaymentStatus
    from app.models.vpn_key import VpnKey, VpnKeyStatus

    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    new_today_r = await db.execute(select(func.count()).select_from(User).where(User.created_at >= today_start))
    rev_today_r = await db.execute(select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.status == PaymentStatus.SUCCEEDED.value, Payment.created_at >= today_start))
    expired_r = await db.execute(select(func.count()).select_from(VpnKey).where(VpnKey.status == VpnKeyStatus.EXPIRED.value))

    rev_week = []
    for i in range(6, -1, -1):
        day_start = today_start - timedelta(days=i)
        day_end = day_start + timedelta(days=1)
        r = await db.execute(select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.status == PaymentStatus.SUCCEEDED.value, Payment.created_at >= day_start, Payment.created_at < day_end))
        rev_week.append(float(r.scalar_one()))

    return {
        "stats": {
            "total_users": await UserService(db).count_all(),
            "active_subscriptions": await VpnKeyService(db).count_active(),
            "total_revenue": float(await PaymentService(db).total_revenue()),
            "open_tickets": await SupportService(db).count_open(),
            "new_users_today": new_today_r.scalar_one(),
            "revenue_today": float(rev_today_r.scalar_one()),
            "expired_keys": expired_r.scalar_one(),
            "pending_payments": await PaymentService(db).count_by_status(PaymentStatus.PENDING),
        },
        "rev_week": rev_week,
    }


@router.get("/api/app-info")
async def spa_app_info(request: Request):
    """App name, version and panel type for SPA."""
    from app.core.configs.remnawave_config import remnawave as _rw
    return {
        "app_name": config.web.app_name,
        "app_version": config.web.app_version,
        "panel_type": _rw.vpn_panel_type,
    }


@router.post("/api/login")
async def spa_login_json(request: Request):
    """JSON login for SPA — sets session cookie."""
    import hmac as _hmac
    from fastapi.responses import JSONResponse as _JR
    from app.core.configs.remnawave_config import remnawave as _rw
    body = await request.json()
    username = body.get("username", "")
    password = body.get("password", "")
    # Timing-safe comparison to prevent timing attacks
    user_ok = _hmac.compare_digest(username, config.web.web_superadmin_username)
    pass_ok = _hmac.compare_digest(password, config.web.web_superadmin_password.get_secret_value())
    if not (user_ok and pass_ok):
        import asyncio as _aio
        await _aio.sleep(0.5)  # slow down brute force
        return _JR({"ok": False, "error": "Неверный логин или пароль"}, status_code=401)
    token = create_access_token(subject=username)
    resp = _JR({
        "ok": True,
        "username": username,
        "app_name": config.web.app_name,
        "app_version": config.web.app_version,
        "panel_type": _rw.vpn_panel_type,
    })
    resp.set_cookie(
        SESSION_COOKIE, token,
        httponly=True, samesite="lax", max_age=86400,
        secure=False,  # set True in prod behind HTTPS
    )
    return resp


# ── SPA: Users API ────────────────────────────────────────────────────────────

@router.get("/api/users")
async def spa_users(request: Request, page: int = 1, q: str = "", db: AsyncSession = Depends(get_db)):
    _require_auth(request)
    limit = 20
    offset = (page - 1) * limit
    all_users = await UserService(db).get_all(limit=500)
    if q:
        ql = q.lower()
        all_users = [u for u in all_users if ql in (u.full_name or "").lower()
                     or ql in (u.username or "").lower()
                     or ql in str(u.id)]
    total = len(all_users)
    users = all_users[offset:offset + limit]
    return {
        "users": [
            {
                "id": u.id,
                "username": u.username,
                "full_name": u.full_name,
                "is_banned": u.is_banned,
                "balance": float(u.balance or 0),
                "keys_count": len(u.vpn_keys),
                "payments_count": len(u.payments),
                "language": u.language,
            }
            for u in users
        ],
        "total": total,
        "page": page,
        "pages": max(1, (total + limit - 1) // limit),
    }


@router.get("/api/users/{user_id}")
async def spa_user_detail(user_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    _require_auth(request)
    from sqlalchemy import select as _sel
    from app.models.vpn_key import VpnKey
    from app.models.payment import Payment as _Pay
    user = await UserService(db).get_by_id(user_id)
    if not user:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="User not found")
    keys_r = await db.execute(_sel(VpnKey).where(VpnKey.user_id == user_id).order_by(VpnKey.id.desc()))
    pays_r = await db.execute(_sel(_Pay).where(_Pay.user_id == user_id).order_by(_Pay.created_at.desc()))
    keys = list(keys_r.scalars().all())
    pays = list(pays_r.scalars().all())
    return {
        "id": user.id,
        "username": user.username,
        "full_name": user.full_name,
        "is_banned": user.is_banned,
        "balance": float(user.balance or 0),
        "language": user.language,
        "keys": [
            {
                "id": k.id,
                "name": k.name,
                "status": k.status,
                "expires_at": k.expires_at.isoformat() if k.expires_at else None,
                "access_url": k.access_url,
            }
            for k in keys
        ],
        "payments": [
            {
                "id": p.id,
                "provider": p.provider,
                "amount": float(p.amount),
                "currency": p.currency,
                "status": p.status,
                "created_at": p.created_at.isoformat() if p.created_at else None,
            }
            for p in pays
        ],
    }


@router.post("/api/users/{user_id}/ban")
async def spa_ban_user(user_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    _require_auth(request)
    from fastapi.responses import JSONResponse as _JR
    if user_id in config.telegram.telegram_admin_ids:
        return _JR({"ok": False, "error": "Cannot ban admin"}, status_code=400)
    user = await UserService(db).ban(user_id)
    if not user:
        return _JR({"ok": False}, status_code=404)
    await db.commit()
    ban_msg = await BotSettingsService(db).get("ban_message") or "🚫 Ваш аккаунт заблокирован."
    await TelegramNotifyService().send_message(user_id, ban_msg)
    return {"ok": True}


@router.post("/api/users/{user_id}/unban")
async def spa_unban_user(user_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    _require_auth(request)
    user = await UserService(db).unban(user_id)
    if not user:
        from fastapi import HTTPException
        raise HTTPException(status_code=404)
    await db.commit()
    unban_msg = await BotSettingsService(db).get("unban_message") or "✅ Ваш аккаунт разблокирован."
    await TelegramNotifyService().send_message(user_id, unban_msg)
    return {"ok": True}


@router.post("/api/users/{user_id}/add-balance")
async def spa_add_balance(user_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    _require_auth(request)
    body = await request.json()
    amount = Decimal(str(body.get("amount", 0)))
    await UserService(db).add_balance(user_id, amount)
    await db.commit()
    await TelegramNotifyService().send_message(user_id, f"💰 На ваш баланс зачислено <b>{amount} ₽</b>")
    return {"ok": True}


# ── SPA: Payments API ─────────────────────────────────────────────────────────

@router.get("/api/payments")
async def spa_payments(request: Request, page: int = 1, status: str = "", db: AsyncSession = Depends(get_db)):
    _require_auth(request)
    from app.models.payment import PaymentStatus as _PS
    limit = 25
    offset = (page - 1) * limit
    ps = _PS(status) if status else None
    all_pays = await PaymentService(db).get_all(limit=500, status=ps)
    total = len(all_pays)
    pays = all_pays[offset:offset + limit]
    return {
        "payments": [
            {
                "id": p.id,
                "user_id": p.user_id,
                "provider": p.provider,
                "amount": float(p.amount),
                "currency": p.currency,
                "status": p.status,
                "created_at": p.created_at.isoformat() if p.created_at else None,
            }
            for p in pays
        ],
        "total": total,
        "page": page,
        "pages": max(1, (total + limit - 1) // limit),
    }


# ── SPA: Subscriptions API ────────────────────────────────────────────────────

@router.get("/api/subscriptions")
async def spa_subscriptions(request: Request, page: int = 1, db: AsyncSession = Depends(get_db)):
    _require_auth(request)
    limit = 25
    offset = (page - 1) * limit
    all_keys = await VpnKeyService(db).get_all(limit=500)
    total = len(all_keys)
    keys = all_keys[offset:offset + limit]
    return {
        "subscriptions": [
            {
                "id": k.id,
                "user_id": k.user_id,
                "name": k.name,
                "status": k.status,
                "expires_at": k.expires_at.isoformat() if k.expires_at else None,
                "access_url": k.access_url,
                "price": float(k.price or 0),
            }
            for k in keys
        ],
        "total": total,
        "page": page,
        "pages": max(1, (total + limit - 1) // limit),
    }


@router.post("/api/subscriptions/{key_id}/extend")
async def spa_extend_sub(key_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    _require_auth(request)
    body = await request.json()
    days = int(body.get("days", 30))
    key = await VpnKeyService(db).extend(key_id, days)
    if not key:
        from fastapi import HTTPException
        raise HTTPException(status_code=404)
    await db.commit()
    return {"ok": True, "expires_at": key.expires_at.isoformat() if key.expires_at else None}


@router.post("/api/subscriptions/{key_id}/cancel")
async def spa_cancel_sub(key_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    _require_auth(request)
    key = await VpnKeyService(db).revoke(key_id)
    if not key:
        from fastapi import HTTPException
        raise HTTPException(status_code=404)
    await db.commit()
    return {"ok": True}


# ── SPA: Plans API ────────────────────────────────────────────────────────────

@router.get("/api/plans")
async def spa_plans(request: Request, db: AsyncSession = Depends(get_db)):
    _require_auth(request)
    plans = await PlanService(db).get_all()
    return {
        "plans": [
            {
                "id": p.id,
                "name": p.name,
                "slug": p.slug,
                "price": float(p.price),
                "currency": p.currency,
                "duration_days": p.duration_days,
                "is_active": p.is_active,
                "description": p.description,
            }
            for p in plans
        ]
    }


@router.post("/api/plans")
async def spa_create_plan(request: Request, db: AsyncSession = Depends(get_db)):
    _require_auth(request)
    import re as _re
    body = await request.json()
    name = body.get("name", "")
    price = Decimal(str(body.get("price", 0)))
    duration_days = int(body.get("duration_days", 30))
    description = body.get("description") or None
    slug = _re.sub(r"[^a-z0-9]+", "_", name.lower().strip()).strip("_") or "plan"
    existing = await PlanService(db).get_by_slug(slug)
    if existing:
        import time as _t
        slug = f"{slug}_{int(_t.time()) % 10000}"
    plan = await PlanService(db).create(name=name, slug=slug, duration_days=duration_days,
                                         price=price, description=description)
    await db.commit()
    return {"ok": True, "id": plan.id}


@router.delete("/api/plans/{plan_id}")
async def spa_delete_plan(plan_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    _require_auth(request)
    await PlanService(db).delete(plan_id)
    await db.commit()
    return {"ok": True}


@router.patch("/api/plans/{plan_id}/toggle")
async def spa_toggle_plan(plan_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    _require_auth(request)
    plan = await PlanService(db).toggle_active(plan_id)
    await db.commit()
    return {"ok": True, "is_active": plan.is_active if plan else False}


# ── SPA: Panel stats (Marzban / Remnawave) ────────────────────────────────────

@router.get("/api/panel-stats")
async def spa_panel_stats(request: Request):
    _require_auth(request)
    from app.services.pasarguard.pasarguard import get_vpn_panel
    from app.core.configs.remnawave_config import remnawave as _rw
    try:
        stats = await get_vpn_panel().get_system_stats()
        return {"ok": True, "panel_type": _rw.vpn_panel_type, "stats": stats}
    except Exception as e:
        return {"ok": False, "panel_type": _rw.vpn_panel_type, "error": str(e)}


# ── SPA: Panel migration (Marzban → Remnawave or vice versa) ──────────────────

@router.post("/api/panel-migrate")
async def spa_panel_migrate(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Migrate all active VPN keys from current panel to the other panel.
    Reads all active keys from DB, re-creates them in the target panel,
    updates access_url in DB.
    """
    _require_auth(request)
    from fastapi.responses import JSONResponse as _JR
    from app.core.configs.remnawave_config import remnawave as _rw
    from app.services.pasarguard.pasarguard import get_vpn_panel, PasarguardService
    from app.services.remnawave.remnawave import RemnawaveService
    from sqlalchemy import select as _sel
    from app.models.vpn_key import VpnKey, VpnKeyStatus
    from datetime import datetime, timezone

    body = await request.json()
    direction = body.get("direction", "")  # "to_remnawave" or "to_marzban"

    if direction == "to_remnawave":
        try:
            target = RemnawaveService()
        except Exception as e:
            return _JR({"ok": False, "error": f"Remnawave not configured: {e}"}, status_code=400)
    elif direction == "to_marzban":
        try:
            target = PasarguardService()
        except Exception as e:
            return _JR({"ok": False, "error": f"Marzban not configured: {e}"}, status_code=400)
    else:
        return _JR({"ok": False, "error": "direction must be 'to_remnawave' or 'to_marzban'"}, status_code=400)

    result = await db.execute(
        _sel(VpnKey).where(
            VpnKey.status == VpnKeyStatus.ACTIVE.value,
            VpnKey.pasarguard_key_id.isnot(None),
        )
    )
    keys = list(result.scalars().all())

    migrated, errors = 0, 0
    now = datetime.now(timezone.utc)

    for key in keys:
        try:
            expire_days = 0
            if key.expires_at:
                delta = key.expires_at - now
                expire_days = max(1, delta.days)

            new_user = await target.create_user(
                username=key.pasarguard_key_id,
                expire_days=expire_days,
                data_limit_gb=0,
            )
            sub_url = new_user.get("subscription_url", "")
            if sub_url:
                key.access_url = sub_url if sub_url.startswith("http") else f"{sub_url}"
            migrated += 1
        except Exception as e:
            log.warning(f"[migrate] key {key.id} failed: {e}")
            errors += 1

    await db.commit()
    return {"ok": True, "migrated": migrated, "errors": errors, "total": len(keys)}


# ── Users ─────────────────────────────────────────────────────────────────────

@router.get("/users", response_class=HTMLResponse)
async def users_page(request: Request, db: AsyncSession = Depends(get_db)):
    _require_auth(request)
    ctx = await _base_ctx(request, db, "users")
    raw = await UserService(db).get_all(limit=200)
    ctx["users"] = [_to_detail(u) for u in raw]
    ctx["plans"] = await PlanService(db).get_all(only_active=True)
    return templates.TemplateResponse("users.html", ctx)


@router.get("/users/search", response_class=HTMLResponse)
async def users_search(request: Request, q: str = "", db: AsyncSession = Depends(get_db)):
    _require_auth(request)
    raw = await UserService(db).get_all(limit=200)
    q = q.lower()
    filtered = [u for u in raw if q in (u.full_name or "").lower() or q in (u.username or "").lower()]
    return templates.TemplateResponse(
        "partials/users_rows.html", {"request": request, "users": [_to_detail(u) for u in filtered]}
    )


@router.get("/users/{user_id}", response_class=HTMLResponse)
async def user_detail_page(user_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    _require_auth(request)
    ctx = await _base_ctx(request, db, "users")
    from sqlalchemy import select
    from app.models.vpn_key import VpnKey
    from app.models.payment import Payment

    user = await UserService(db).get_by_id(user_id)
    if not user:
        return HTMLResponse("Пользователь не найден", status_code=404)

    keys_result = await db.execute(
        select(VpnKey).where(VpnKey.user_id == user_id).order_by(VpnKey.id.desc())
    )
    pays_result = await db.execute(
        select(Payment).where(Payment.user_id == user_id).order_by(Payment.created_at.desc())
    )

    ctx["user"] = UserRead.model_validate(user)
    ctx["vpn_keys"] = list(keys_result.scalars().all())
    ctx["payments"] = list(pays_result.scalars().all())
    ctx["plans"] = await PlanService(db).get_all(only_active=True)
    return templates.TemplateResponse("user_detail.html", ctx)


@router.post("/users/{user_id}/deduct-balance", response_class=HTMLResponse)
async def deduct_balance(
    user_id: int, request: Request,
    amount: Decimal = Form(...),
    db: AsyncSession = Depends(get_db),
):
    _require_auth(request)
    user = await UserService(db).deduct_balance(user_id, amount)
    if not user:
        resp = Response(status_code=400)
        _toast(resp, "Недостаточно средств на балансе", "error")
        return resp
    await db.commit()
    notify = TelegramNotifyService()
    await notify.send_message(user_id, f"💸 С вашего баланса списано <b>{amount} ₽</b>")
    resp = Response(status_code=200)
    _toast(resp, f"Снято {amount} ₽ с баланса")
    return resp


@router.post("/users/{user_id}/ban", response_class=HTMLResponse)
async def ban_user_view(user_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    _require_auth(request)
    if user_id in config.telegram.telegram_admin_ids:
        resp = Response(status_code=400)
        _toast(resp, "Нельзя забанить администратора", "error")
        return resp
    user = await UserService(db).ban(user_id)
    if not user:
        return HTMLResponse("", status_code=404)
    await db.commit()
    ban_msg = await BotSettingsService(db).get("ban_message") or "🚫 Ваш аккаунт заблокирован."
    await TelegramNotifyService().send_message(user_id, ban_msg)
    resp = templates.TemplateResponse("partials/users_rows.html", {"request": request, "users": [_to_detail(user)]})
    _toast(resp, "Пользователь заблокирован")
    return resp


@router.post("/users/{user_id}/unban", response_class=HTMLResponse)
async def unban_user_view(user_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    _require_auth(request)
    user = await UserService(db).unban(user_id)
    if not user:
        return HTMLResponse("", status_code=404)
    await db.commit()
    unban_msg = await BotSettingsService(db).get("unban_message") or "✅ Ваш аккаунт разблокирован. Добро пожаловать обратно!"
    await TelegramNotifyService().send_message(user_id, unban_msg)
    resp = templates.TemplateResponse("partials/users_rows.html", {"request": request, "users": [_to_detail(user)]})
    _toast(resp, "Пользователь разблокирован")
    return resp


@router.post("/users/{user_id}/gift-subscription", response_class=HTMLResponse)
async def gift_subscription(
    user_id: int, request: Request,
    plan_id: int = Form(...),
    db: AsyncSession = Depends(get_db),
):
    _require_auth(request)
    plan = await PlanService(db).get_by_id(plan_id)
    if not plan:
        return HTMLResponse("", status_code=404)
    key = await VpnKeyService(db).provision(user_id=user_id, plan=plan)
    await db.commit()
    if key:
        await TelegramNotifyService().send_message(
            user_id,
            f"🎁 <b>Вам подарена подписка!</b>\n\nПлан: <b>{plan.name}</b> ({plan.duration_days} дней)\n\n"
            f"🔑 <b>Ссылка:</b>\n<code>{key.access_url}</code>",
        )
    resp = Response(status_code=200)
    _toast(resp, f"Подписка «{plan.name}» подарена" if key else "Ошибка создания ключа в Marzban",
           "success" if key else "error")
    return resp


@router.post("/users/{user_id}/add-balance", response_class=HTMLResponse)
async def add_balance(
    user_id: int, request: Request,
    amount: Decimal = Form(...),
    db: AsyncSession = Depends(get_db),
):
    _require_auth(request)
    await UserService(db).add_balance(user_id, amount)
    notify = TelegramNotifyService()
    await notify.send_message(user_id, f"💰 На ваш баланс зачислено <b>{amount} ₽</b>")
    resp = Response(status_code=200)
    _toast(resp, f"Баланс пополнен на {amount} ₽")
    return resp


def _to_detail(u) -> UserDetail:
    return UserDetail(
        **UserRead.model_validate(u).model_dump(),
        subscriptions_count=len(u.vpn_keys),
        payments_count=len(u.payments),
        vpn_keys_count=len(u.vpn_keys),
    )


def _render_messages(ticket) -> str:
    """Render ticket messages as HTML for HTMX swap."""
    if not ticket:
        return ""
    msgs_html = ""
    for msg in ticket.messages:
        align = "justify-content-end" if msg.is_admin else ""
        bg = "rgba(108,99,255,.2)" if msg.is_admin else "rgba(255,255,255,.05)"
        sender = (
            '<i class="bi bi-shield-check me-1" style="color:#6c63ff"></i>Поддержка'
            if msg.is_admin
            else f'<i class="bi bi-person me-1"></i>Пользователь {msg.sender_id}'
        )
        reply_btn = ""
        if not msg.is_admin:
            reply_btn = (
                f'<div class="mt-1 text-end">'
                f'<button class="btn btn-sm py-0 px-2" style="font-size:.65rem;color:#6c63ff;background:none;border:1px solid rgba(108,99,255,.3)" '
                f'onclick="document.querySelector(\'[name=text]\').value=\'\'">✏️ Ответить</button>'
                f'</div>'
            )
        msgs_html += (
            f'<div class="mb-3 d-flex {align}">'
            f'<div style="max-width:80%;background:{bg};border-radius:10px;padding:.6rem .9rem;font-size:.85rem;color:#c8d0e0">'
            f'<div style="font-size:.7rem;color:#8892a4;margin-bottom:.3rem">{sender}</div>'
            f'{msg.text}{reply_btn}</div></div>'
        )
    return msgs_html


# ── Plans ─────────────────────────────────────────────────────────────────────

@router.get("/plans", response_class=HTMLResponse)
async def plans_page(request: Request, db: AsyncSession = Depends(get_db)):
    _require_auth(request)
    ctx = await _base_ctx(request, db, "plans")
    ctx["plans"] = await PlanService(db).get_all()
    return templates.TemplateResponse("plans.html", ctx)


@router.post("/plans", response_class=HTMLResponse)
async def create_plan_view(
    request: Request,
    name: str = Form(...),
    price: Decimal = Form(...),
    duration_days: int = Form(...),
    description: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
):
    _require_auth(request)
    import re
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower().strip()).strip("_") or "plan"
    # ensure unique slug
    existing = await PlanService(db).get_by_slug(slug)
    if existing:
        import time
        slug = f"{slug}_{int(time.time()) % 10000}"
    await PlanService(db).create(
        name=name, slug=slug, duration_days=duration_days,
        price=price, description=description or None,
    )
    await db.commit()
    plans = await PlanService(db).get_all()
    resp = templates.TemplateResponse("partials/plans_grid.html", {"request": request, "plans": plans})
    _toast(resp, f"Тариф «{name}» создан")
    return resp


@router.post("/plans/{plan_id}/edit", response_class=HTMLResponse)
async def edit_plan_view(
    plan_id: int, request: Request,
    name: str = Form(...),
    price: Decimal = Form(...),
    duration_days: int = Form(...),
    description: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
):
    _require_auth(request)
    plan = await PlanService(db).update(
        plan_id, name=name, price=price,
        duration_days=duration_days, description=description or None,
    )
    await db.commit()
    plans = await PlanService(db).get_all()
    resp = templates.TemplateResponse("partials/plans_grid.html", {"request": request, "plans": plans})
    _toast(resp, f"Тариф «{plan.name if plan else plan_id}» обновлён")
    return resp


@router.post("/plans/{plan_id}/toggle", response_class=HTMLResponse)
async def toggle_plan_view(plan_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    _require_auth(request)
    plan = await PlanService(db).toggle_active(plan_id)
    if not plan:
        return HTMLResponse("", status_code=404)
    status_label = "active" if plan.is_active else "closed"
    status_text = "Активен" if plan.is_active else "Отключён"
    icon = "pause" if plan.is_active else "play"
    html = f"""<div class="col-md-6 col-xl-4" id="plan-{plan.id}">
      <div class="card h-100 p-3">
        <div class="d-flex align-items-start justify-content-between mb-2">
          <div><div class="fw-bold text-white">{plan.name}</div>
          <code style="font-size:.7rem;color:#8892a4">{plan.slug}</code></div>
          <span class="badge badge-custom badge-{status_label}">{status_text}</span>
        </div>
        <div class="d-flex gap-3 mb-3" style="font-size:.8rem;color:#8892a4">
          <span><i class="bi bi-clock me-1"></i>{plan.duration_days} дн.</span>
          <span><i class="bi bi-currency-ruble me-1"></i>{plan.price} {plan.currency}</span>
        </div>
        <div class="d-flex gap-2 mt-auto">
          <button class="btn btn-sm btn-outline-secondary"
            hx-post="/panel/plans/{plan.id}/toggle" hx-target="#plan-{plan.id}" hx-swap="outerHTML">
            <i class="bi bi-{icon}"></i>
          </button>
        </div>
      </div>
    </div>"""
    resp = HTMLResponse(html)
    _toast(resp, f"Tариф {'включён' if plan.is_active else 'отключён'}")
    return resp


@router.delete("/plans/{plan_id}", response_class=HTMLResponse)
async def delete_plan_view(plan_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    _require_auth(request)
    await PlanService(db).delete(plan_id)
    resp = HTMLResponse("")
    _toast(resp, "Тариф удалён")
    return resp


# ── Payments ──────────────────────────────────────────────────────────────────

@router.get("/payments", response_class=HTMLResponse)
async def payments_page(request: Request, status: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    _require_auth(request)
    ctx = await _base_ctx(request, db, "payments")
    ps = PaymentStatus(status) if status else None
    ctx["payments"] = await PaymentService(db).get_all(limit=200, status=ps)
    return templates.TemplateResponse("payments.html", ctx)


@router.post("/payments/{payment_id}/refund", response_class=HTMLResponse)
async def refund_payment_view(payment_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    _require_auth(request)
    payment = await PaymentService(db).refund(payment_id)
    if not payment:
        return HTMLResponse("", status_code=404)
    resp = HTMLResponse(f"""<tr>
      <td><code style="color:#6c63ff">#{payment.id}</code></td>
      <td><a href="/panel/users/{payment.user_id}" style="color:#6c63ff">{payment.user_id}</a></td>
      <td><span style="color:#8892a4;font-size:.8rem">{payment.provider}</span></td>
      <td><b>{payment.amount}</b> {payment.currency}</td>
      <td><span class="badge badge-custom badge-open">Возврат</span></td>
      <td style="color:#8892a4;font-size:.8rem">—</td><td></td></tr>""")
    _toast(resp, f"Возврат платежа #{payment_id} выполнен")
    return resp


# ── Subscriptions (VPN Keys) ──────────────────────────────────────────────────

@router.get("/subscriptions", response_class=HTMLResponse)
async def subscriptions_page(request: Request, db: AsyncSession = Depends(get_db)):
    _require_auth(request)
    ctx = await _base_ctx(request, db, "subscriptions")
    ctx["subscriptions"] = await VpnKeyService(db).get_all(limit=200)
    ctx["plans"] = await PlanService(db).get_all(only_active=True)
    return templates.TemplateResponse("subscriptions.html", ctx)


@router.post("/subscriptions/create", response_class=HTMLResponse)
async def create_subscription(
    request: Request,
    user_id: int = Form(...),
    plan_id: int = Form(...),
    db: AsyncSession = Depends(get_db),
):
    _require_auth(request)
    plan = await PlanService(db).get_by_id(plan_id)
    if not plan:
        resp = Response(status_code=404)
        _toast(resp, "Тариф не найден", "error")
        return resp
    key = await VpnKeyService(db).provision(user_id=user_id, plan=plan)
    await db.commit()
    if key:
        await TelegramNotifyService().send_message(
            user_id,
            f"🎁 <b>Вам выдана подписка!</b>\n\nПлан: <b>{plan.name}</b> ({plan.duration_days} дней)\n\n"
            f"🔑 <b>Ссылка:</b>\n<code>{key.access_url}</code>",
        )
    resp = Response(status_code=200)
    _toast(resp, f"Подписка «{plan.name}» создана" if key else "Ошибка создания ключа в Marzban",
           "success" if key else "error")
    return resp


@router.post("/subscriptions/{key_id}/extend", response_class=HTMLResponse)
async def extend_subscription(
    key_id: int, request: Request,
    days: int = Form(...),
    db: AsyncSession = Depends(get_db),
):
    _require_auth(request)
    key = await VpnKeyService(db).extend(key_id, days)
    if not key:
        resp = Response(status_code=404)
        _toast(resp, "Подписка не найдена", "error")
        return resp
    await db.commit()
    exp_str = key.expires_at.strftime('%d.%m.%Y') if key.expires_at else '—'
    await TelegramNotifyService().send_message(
        key.user_id,
        f"📅 <b>Ваша подписка продлена на {days} дней!</b>\n\nДействует до: {exp_str}",
    )
    resp = Response(status_code=200)
    _toast(resp, f"Подписка #{key_id} продлена на {days} дней")
    return resp


@router.post("/subscriptions/{key_id}/cancel", response_class=HTMLResponse)
async def cancel_subscription(key_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    _require_auth(request)
    key = await VpnKeyService(db).revoke(key_id)
    if not key:
        resp = Response(status_code=404)
        _toast(resp, "Подписка не найдена", "error")
        return resp
    await db.commit()
    await TelegramNotifyService().send_message(key.user_id, "❌ <b>Ваша подписка отменена.</b>")
    resp = Response(status_code=200)
    _toast(resp, f"Подписка #{key_id} отменена")
    return resp


# ── Promo codes ───────────────────────────────────────────────────────────────

@router.get("/promos", response_class=HTMLResponse)
async def promos_page(request: Request, db: AsyncSession = Depends(get_db)):
    _require_auth(request)
    ctx = await _base_ctx(request, db, "promos")
    ctx["promos"] = await PromoService(db).get_all()
    ctx["plans"] = await PlanService(db).get_all(only_active=True)
    return templates.TemplateResponse("promos.html", ctx)


@router.post("/promos", response_class=HTMLResponse)
async def create_promo(
    request: Request,
    code: str = Form(...),
    promo_type: str = Form(...),
    value: Decimal = Form(...),
    plan_id: Optional[int] = Form(None),
    max_uses: int = Form(0),
    db: AsyncSession = Depends(get_db),
):
    _require_auth(request)
    await PromoService(db).create(
        code=code.upper().strip(),
        promo_type=promo_type,
        value=value,
        plan_id=plan_id,
        max_uses=max_uses,
    )
    await db.commit()
    promos = await PromoService(db).get_all()
    plans = await PlanService(db).get_all(only_active=True)
    resp = templates.TemplateResponse(
        "partials/promos_table.html", {"request": request, "promos": promos, "plans": plans}
    )
    _toast(resp, f"Промокод {code.upper()} создан")
    return resp


@router.delete("/promos/{promo_id}", response_class=HTMLResponse)
async def delete_promo(promo_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    _require_auth(request)
    await PromoService(db).delete(promo_id)
    await db.commit()
    resp = HTMLResponse("")
    _toast(resp, "Промокод удалён")
    return resp


@router.post("/promos/{promo_id}/toggle", response_class=HTMLResponse)
async def toggle_promo(promo_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    _require_auth(request)
    promo = await PromoService(db).toggle_active(promo_id)
    await db.commit()
    if not promo:
        return HTMLResponse("", status_code=404)
    promos = await PromoService(db).get_all()
    plans = await PlanService(db).get_all(only_active=True)
    resp = templates.TemplateResponse(
        "partials/promos_table.html", {"request": request, "promos": promos, "plans": plans}
    )
    _toast(resp, "Статус промокода обновлён")
    return resp


# ── Referrals ─────────────────────────────────────────────────────────────────

@router.get("/referrals", response_class=HTMLResponse)
async def referrals_page(request: Request, db: AsyncSession = Depends(get_db)):
    _require_auth(request)
    ctx = await _base_ctx(request, db, "referrals")
    ctx["stats"] = await ReferralService(db).get_stats()
    ctx["top"] = await ReferralService(db).get_top(limit=20)
    return templates.TemplateResponse("referrals.html", ctx)


# ── Support ───────────────────────────────────────────────────────────────────

@router.get("/support", response_class=HTMLResponse)
async def support_page(request: Request, status: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    _require_auth(request)
    ctx = await _base_ctx(request, db, "support")
    ts = TicketStatus(status) if status else None
    ctx["tickets"] = await SupportService(db).get_all(status=ts, limit=100)
    ctx["ticket"] = None
    ctx["current_status"] = status or ""
    ctx["selected_id"] = None
    return templates.TemplateResponse("support.html", ctx)


@router.get("/support/{ticket_id}", response_class=HTMLResponse)
async def support_ticket(ticket_id: int, request: Request, status: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    _require_auth(request)
    ctx = await _base_ctx(request, db, "support")
    ts = TicketStatus(status) if status else None
    ctx["tickets"] = await SupportService(db).get_all(status=ts, limit=100)
    ctx["ticket"] = await SupportService(db).get_by_id(ticket_id)
    ctx["current_status"] = status or ""
    ctx["selected_id"] = ticket_id
    return templates.TemplateResponse("support.html", ctx)


@router.post("/support/{ticket_id}/reply", response_class=HTMLResponse)
async def support_reply(
    ticket_id: int, request: Request,
    text: str = Form(...),
    notify_user: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
):
    _require_auth(request)
    svc = SupportService(db)
    await svc.add_message(ticket_id=ticket_id, sender_id=0, text=text, is_admin=True)
    await db.commit()

    ticket = await svc.get_by_id(ticket_id)
    if notify_user is not None and ticket:
        await TelegramNotifyService().send_message(
            ticket.user_id, f"💬 <b>Ответ по тикету #{ticket_id}</b>\n\n{text}"
        )

    msgs_html = _render_messages(ticket)
    resp = HTMLResponse(msgs_html)
    _toast(resp, "Ответ отправлен")
    return resp


@router.post("/support/{ticket_id}/close", response_class=HTMLResponse)
async def support_close(ticket_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    _require_auth(request)
    svc = SupportService(db)
    ticket = await svc.get_by_id(ticket_id)
    if not ticket:
        resp = Response(status_code=404)
        _toast(resp, "Тикет не найден", "error")
        return resp
    await svc.set_status(ticket_id, TicketStatus.CLOSED)
    await db.commit()
    # Notify user
    await TelegramNotifyService().send_message(
        ticket.user_id,
        f"🔒 <b>Тикет #{ticket_id} закрыт поддержкой.</b>\n\nЕсли у вас остались вопросы — создайте новое обращение.",
    )
    resp = Response(status_code=200)
    _toast(resp, f"Тикет #{ticket_id} закрыт, пользователь уведомлён")
    return resp


@router.patch("/support/{ticket_id}/status")
async def support_status(ticket_id: int, request: Request, status: str = Form(...), db: AsyncSession = Depends(get_db)):
    _require_auth(request)
    await SupportService(db).set_status(ticket_id, TicketStatus(status))
    await db.commit()
    resp = Response(status_code=200)
    _toast(resp, "Статус обновлён")
    return resp


@router.patch("/support/{ticket_id}/priority")
async def support_priority(ticket_id: int, request: Request, priority: str = Form(...), db: AsyncSession = Depends(get_db)):
    _require_auth(request)
    await SupportService(db).set_priority(ticket_id, TicketPriority(priority))
    await db.commit()
    resp = Response(status_code=200)
    _toast(resp, "Приоритет обновлён")
    return resp


# ── VPN Keys ──────────────────────────────────────────────────────────────────

@router.get("/vpn", response_class=HTMLResponse)
async def vpn_page(request: Request, db: AsyncSession = Depends(get_db)):
    _require_auth(request)
    ctx = await _base_ctx(request, db, "vpn")
    ctx["keys"] = await VpnKeyService(db).get_all(limit=200)
    return templates.TemplateResponse("vpn.html", ctx)


@router.post("/vpn/{key_id}/revoke", response_class=HTMLResponse)
async def revoke_vpn_key(key_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    _require_auth(request)
    key = await VpnKeyService(db).revoke(key_id)
    await db.commit()
    resp = Response(status_code=200)
    _toast(resp, f"Ключ #{key_id} отозван" if key else "Ключ не найден",
           "success" if key else "error")
    return resp


@router.post("/vpn/{key_id}/delete", response_class=HTMLResponse)
async def delete_vpn_key(key_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    _require_auth(request)
    key = await VpnKeyService(db).delete_from_marzban(key_id)
    await db.commit()
    resp = HTMLResponse("")
    _toast(resp, f"Ключ #{key_id} удалён из Marzban" if key else "Ключ не найден",
           "success" if key else "error")
    return resp


@router.post("/vpn/sync", response_class=HTMLResponse)
async def sync_vpn_keys(request: Request, db: AsyncSession = Depends(get_db)):
    _require_auth(request)
    result = await VpnKeyService(db).sync_from_marzban()
    await db.commit()
    resp = Response(status_code=200)
    _toast(resp, f"Синхронизировано: {result['synced']}, ошибок: {result['errors']}")
    return resp

@router.get("/broadcasts", response_class=HTMLResponse)
async def broadcasts_page(request: Request, db: AsyncSession = Depends(get_db)):
    _require_auth(request)
    ctx = await _base_ctx(request, db, "broadcasts")
    ctx["broadcasts"] = await BroadcastService(db).get_all()
    return templates.TemplateResponse("broadcasts.html", ctx)


@router.post("/broadcasts", response_class=HTMLResponse)
async def create_broadcast_view(
    request: Request,
    title: str = Form(...), text: str = Form(...),
    target: str = Form("all"), parse_mode: str = Form("HTML"),
    db: AsyncSession = Depends(get_db),
):
    _require_auth(request)
    await BroadcastService(db).create(title=title, text=text, target=target, parse_mode=parse_mode)
    resp = templates.TemplateResponse(
        "partials/broadcasts_list.html",
        {"request": request, "broadcasts": await BroadcastService(db).get_all()},
    )
    _toast(resp, "Черновик создан")
    return resp


@router.post("/broadcasts/{broadcast_id}/send", response_class=HTMLResponse)
async def send_broadcast_view(broadcast_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    _require_auth(request)
    bc = await BroadcastService(db).send(broadcast_id)
    if not bc:
        return HTMLResponse("", status_code=400)
    resp = templates.TemplateResponse(
        "partials/broadcasts_list.html", {"request": request, "broadcasts": [bc]}
    )
    _toast(resp, f"Отправлено: {bc.sent_count}, ошибок: {bc.failed_count}")
    return resp


# ── Telegram ──────────────────────────────────────────────────────────────────

@router.get("/telegram", response_class=HTMLResponse)
async def telegram_page(request: Request, db: AsyncSession = Depends(get_db)):
    _require_auth(request)
    ctx = await _base_ctx(request, db, "telegram")
    ctx["bot_info"] = await TelegramNotifyService().get_bot_info()
    ctx["admin_ids"] = config.telegram.telegram_admin_ids
    ctx["bot_settings"] = await BotSettingsService(db).get_all()

    # Keyboard editor data
    import json as _json
    ctx["all_buttons"] = _ALL_BUTTONS
    ctx["default_layout"] = _DEFAULT_LAYOUT
    raw = await BotSettingsService(db).get("keyboard_layout")
    try:
        ctx["layout"] = _json.loads(raw) if raw else _DEFAULT_LAYOUT
    except Exception:
        ctx["layout"] = _DEFAULT_LAYOUT

    from app.services.pasarguard.pasarguard import PasarguardService
    marzban = PasarguardService()
    try:
        stats = await marzban.get_system_stats()
        ctx["marzban_ok"] = True
        ctx["marzban_stats"] = stats
    except Exception:
        ctx["marzban_ok"] = False
        ctx["marzban_stats"] = None

    return templates.TemplateResponse("telegram.html", ctx)


@router.post("/telegram/test-marzban", response_class=HTMLResponse)
async def test_marzban(request: Request):
    _require_auth(request)
    from app.services.pasarguard.pasarguard import PasarguardService
    marzban = PasarguardService()
    try:
        stats = await marzban.get_system_stats()
        users_active = stats.get("users_active", 0)
        total_user = stats.get("total_user", 0)
        incoming = round((stats.get("incoming_bandwidth", 0) or 0) / 1073741824, 2)
        outgoing = round((stats.get("outgoing_bandwidth", 0) or 0) / 1073741824, 2)
        ram_mb = (stats.get("mem_used", 0) or 0) // 1048576
        cpu = round(stats.get("cpu_usage", 0) or 0, 1)

        items = [
            ("bi-wifi",            "rgba(34,197,94,.1)",   "#22c55e", "Онлайн",      users_active),
            ("bi-people",          "rgba(108,99,255,.1)",  "#a78bfa", "Всего юзеров", total_user),
            ("bi-arrow-down-circle","rgba(59,130,246,.1)", "#3b82f6", "Входящий",    f"{incoming} GB"),
            ("bi-arrow-up-circle", "rgba(239,68,68,.1)",   "#ef4444", "Исходящий",   f"{outgoing} GB"),
            ("bi-memory",          "rgba(234,179,8,.1)",   "#eab308", "RAM",          f"{ram_mb} MB"),
            ("bi-cpu",             "rgba(108,99,255,.1)",  "#a78bfa", "CPU",          f"{cpu}%"),
        ]

        cards = ""
        for icon, bg, color, label, val in items:
            cards += f"""<div class="col-6">
              <div style="background:{bg};border-radius:8px;padding:.5rem .75rem;font-size:.75rem">
                <div style="color:#8892a4"><i class="bi {icon} me-1" style="color:{color}"></i>{label}</div>
                <div class="text-white fw-semibold">{val}</div>
              </div>
            </div>"""

        html = f"""<div class="d-flex align-items-center gap-2 mb-3" style="color:#22c55e;font-size:.85rem">
          <i class="bi bi-check-circle-fill"></i><span>Подключено</span>
        </div>
        <div class="row g-2">{cards}</div>"""
        resp = HTMLResponse(html)
        _toast(resp, "Marzban подключён")
    except Exception as e:
        html = f"""<div class="d-flex align-items-center gap-2" style="color:#ef4444;font-size:.85rem">
          <i class="bi bi-x-circle-fill"></i><span>Ошибка: {str(e)[:80]}</span>
        </div>"""
        resp = HTMLResponse(html)
        _toast(resp, "Нет подключения к Marzban", "error")
    return resp


@router.get("/telegram/groups", response_class=HTMLResponse)
async def get_marzban_groups(request: Request):
    """HTMX: возвращает список групп из Marzban для отображения чекбоксов."""
    _require_auth(request)
    from app.services.pasarguard.pasarguard import PasarguardService
    groups = await PasarguardService().get_groups()
    if not groups:
        return HTMLResponse('<div style="color:#ef4444;font-size:.8rem"><i class="bi bi-x-circle me-1"></i>Не удалось загрузить группы</div>')

    html = ""
    for g in groups:
        disabled = " (отключена)" if g.get("is_disabled") else ""
        inbounds = ", ".join(g.get("inbound_tags", []))
        html += (
            f'<div class="form-check mb-2">'
            f'<input class="form-check-input" type="checkbox" name="group_id" value="{g["id"]}" id="grp{g["id"]}">'
            f'<label class="form-check-label" for="grp{g["id"]}" style="color:#c8d0e0;font-size:.85rem">'
            f'<b>{g["name"]}</b>{disabled}'
            f'<span style="color:#8892a4;font-size:.75rem;display:block">{inbounds} · {g.get("total_users",0)} юзеров</span>'
            f'</label></div>'
        )
    return HTMLResponse(html)


@router.post("/telegram/groups", response_class=HTMLResponse)
async def save_marzban_groups(request: Request, db: AsyncSession = Depends(get_db)):
    """Сохраняет выбранные group_ids в bot_settings."""
    _require_auth(request)
    import json as _json
    form = await request.form()
    group_ids = [int(v) for v in form.getlist("group_id") if str(v).isdigit()]
    await BotSettingsService(db).set("vpn_group_ids", _json.dumps(group_ids))
    await db.commit()
    resp = Response(status_code=200)
    _toast(resp, f"Группы сохранены: {group_ids if group_ids else 'все (без фильтра)'}")
    return resp


@router.post("/telegram/photo/upload")
async def upload_photo(
    request: Request,
    key: str = Form(...),
    photo: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """
    Загружает фото через Bot API (sendPhoto), получает file_id и сохраняет в bot_settings.
    Отправляет фото первому admin_id чтобы получить file_id от Telegram.
    """
    _require_auth(request)
    from fastapi.responses import JSONResponse
    import httpx

    token = config.telegram.telegram_bot_token.get_secret_value()
    admin_ids = config.telegram.telegram_admin_ids
    if not admin_ids:
        return JSONResponse({"detail": "Нет admin_ids в конфиге"}, status_code=400)

    chat_id = admin_ids[0]
    content = await photo.read()
    filename = photo.filename or "photo.jpg"

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"https://api.telegram.org/bot{token}/sendPhoto",
                data={"chat_id": chat_id, "caption": f"📎 Фото для раздела: {key}"},
                files={"photo": (filename, content, photo.content_type or "image/jpeg")},
            )
        result = resp.json()
        if not result.get("ok"):
            return JSONResponse({"detail": result.get("description", "Ошибка Telegram")}, status_code=400)

        # Берём наибольший размер фото
        photos = result["result"]["photo"]
        file_id = photos[-1]["file_id"]

        await BotSettingsService(db).set(key, file_id)
        await db.commit()
        return JSONResponse({"file_id": file_id})

    except Exception as e:
        return JSONResponse({"detail": str(e)}, status_code=500)


@router.post("/telegram/miniapp", response_class=HTMLResponse)
async def save_miniapp(
    request: Request,
    panel_url: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    """Сохраняет URL панели для кнопки Mini App в /admin команде."""
    _require_auth(request)
    panel_url = panel_url.strip().rstrip("/")
    await BotSettingsService(db).set("panel_url", panel_url)
    await db.commit()
    resp = Response(status_code=200)
    _toast(resp, "URL панели сохранён. Кнопка появится в /admin для администраторов.")
    return resp


@router.post("/telegram/photo/clear")
async def clear_photo(    request: Request,
    key: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    _require_auth(request)
    await BotSettingsService(db).set(key, "")
    await db.commit()
    resp = Response(status_code=200)
    _toast(resp, "Фото удалено")
    return resp


@router.post("/telegram/bot-settings")
async def save_bot_settings(request: Request, db: AsyncSession = Depends(get_db)):
    _require_auth(request)
    form = await request.form()
    allowed_keys = {
        "welcome_message", "btn_my_keys", "btn_buy", "btn_support",
        "btn_balance", "btn_promo", "support_url",
        "referral_bonus_days", "referral_bonus_type", "referral_bonus_value",
        "payment_success_message",
        "ban_message", "bot_disabled_message",
        "subscription_issued_message", "subscription_cancelled_message",
        "referral_welcome_message", "about_text", "unban_message",
        "required_channel_id", "required_channel_name",
        "photo_welcome", "photo_buy", "photo_my_keys",
        "photo_balance", "photo_about", "photo_support", "photo_profile",
        "panel_url", "required_channel_id", "required_channel_name",
        "btn_style_buy", "btn_style_my_keys", "btn_style_support",
        "btn_style_balance", "btn_style_promo", "btn_style_back",
        "btn_style_profile", "btn_style_connect", "btn_style_about",
        "btn_style_servers", "btn_style_top_referrers", "btn_style_status", "btn_style_language",
        "btn_emoji_buy", "btn_emoji_my_keys", "btn_emoji_support",
        "btn_emoji_balance", "btn_emoji_promo",
        "btn_emoji_profile", "btn_emoji_connect", "btn_emoji_about",
        "btn_emoji_servers", "btn_emoji_top_referrers", "btn_emoji_status", "btn_emoji_language",
        "bot_language", "cryptobot_token",
        "trial_enabled", "trial_days", "trial_label",
        "notify_expiry_enabled", "notify_expiry_days", "notify_expiry_message",
    }
    data = {k: v for k, v in form.items() if k in allowed_keys}
    await BotSettingsService(db).set_many(data)
    await db.commit()
    resp = Response(status_code=200)
    _toast(resp, "Настройки бота сохранены")
    return resp


@router.post("/telegram/lang-strings")
async def save_lang_strings(request: Request, db: AsyncSession = Depends(get_db)):
    """Save i18n string overrides for a specific language."""
    _require_auth(request)
    form = await request.form()
    lang = form.get("lang", "ru")
    if lang not in ("ru", "en", "fa"):
        return Response(status_code=400)
    # Whitelist of allowed i18n keys
    allowed_i18n_keys = {
        "welcome", "welcome_back", "btn_my_keys", "btn_buy", "btn_balance",
        "btn_promo", "btn_support", "btn_language", "choose_plan",
        "payment_success", "no_keys", "choose_language", "language_set",
        "main_menu", "enter_promo", "support_title", "support_no_tickets",
        "support_tickets", "new_ticket", "ticket_subject", "ticket_message",
        "ticket_created", "ticket_closed", "ticket_reply_sent", "ticket_not_found",
        "write_reply", "close_ticket", "payment_error", "payment_pending",
        "payment_failed", "payment_go", "payment_check", "pay_card",
        "pay_stars", "pay_crypto", "pay_balance", "no_plans", "key_error",
        "subscription_url", "balance_title", "referrals_count", "referral_bonus",
        "referral_link", "promo_balance", "promo_days", "promo_discount", "promo_invalid",
    }
    svc = BotSettingsService(db)
    for key, value in form.items():
        if key == "lang":
            continue
        if key not in allowed_i18n_keys:
            continue
        val = str(value).strip()
        if val:
            await svc.set(f"i18n_{lang}_{key}", val)
        # If empty, delete override (reset to default) — just don't save empty strings
    await db.commit()
    resp = Response(status_code=200)
    _toast(resp, "Строки языка сохранены")
    return resp


@router.post("/telegram/bot-toggle")
async def bot_toggle(request: Request, db: AsyncSession = Depends(get_db)):
    _require_auth(request)
    svc = BotSettingsService(db)
    current = await svc.get("bot_enabled") or "1"
    new_val = "0" if current == "1" else "1"
    await svc.set("bot_enabled", new_val)
    await db.commit()
    resp = Response(status_code=200)
    _toast(resp, "Бот включён" if new_val == "1" else "Бот отключён")
    return resp


@router.post("/telegram/send")
async def telegram_send_view(request: Request, chat_id: int = Form(...), text: str = Form(...)):
    _require_auth(request)
    ok = await TelegramNotifyService().send_message(chat_id, text)
    resp = Response(status_code=200)
    _toast(resp, "Сообщение отправлено" if ok else "Ошибка отправки", "success" if ok else "error")
    return resp


# ── Backup ────────────────────────────────────────────────────────────────────

@router.get("/backup", response_class=HTMLResponse)
async def backup_page(request: Request, db: AsyncSession = Depends(get_db)):
    _require_auth(request)
    ctx = await _base_ctx(request, db, "backup")
    return templates.TemplateResponse("backup.html", ctx)


@router.get("/backup/export")
async def backup_export(request: Request, format: str = "sql"):
    _require_auth(request)
    db_cfg = config.database
    env = {
        "PGPASSWORD": db_cfg.db_password.get_secret_value(),
        "PATH": "/usr/bin:/bin:/usr/local/bin",
    }
    cmd = [
        "pg_dump",
        "-h", db_cfg.db_host,
        "-p", str(db_cfg.db_port),
        "-U", db_cfg.db_user,
        "-d", db_cfg.db_name,
        "--no-password",
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, env=env, timeout=120)
        if result.returncode != 0:
            err = result.stderr.decode(errors="replace")[:300]
            return Response(content=f"pg_dump error: {err}", status_code=500)
        sql_bytes = result.stdout
    except FileNotFoundError:
        return Response(content="pg_dump not found. Install postgresql-client on the server.", status_code=500)
    except subprocess.TimeoutExpired:
        return Response(content="pg_dump timed out", status_code=500)

    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

    if format == "gz":
        buf = io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
            gz.write(sql_bytes)
        buf.seek(0)
        return StreamingResponse(
            buf,
            media_type="application/gzip",
            headers={"Content-Disposition": f'attachment; filename="backup_{ts}.sql.gz"'},
        )

    return StreamingResponse(
        io.BytesIO(sql_bytes),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="backup_{ts}.sql"'},
    )


@router.post("/backup/import", response_class=HTMLResponse)
async def backup_import(
    request: Request,
    file: UploadFile = File(...),
    confirm: Optional[str] = Form(None),
):
    _require_auth(request)
    if confirm != "yes":
        resp = Response(status_code=400)
        _toast(resp, "Подтвердите восстановление", "error")
        return resp

    content = await file.read()
    filename = file.filename or ""

    # Decompress if gzip
    if filename.endswith(".gz"):
        try:
            content = gzip.decompress(content)
        except Exception:
            resp = Response(status_code=400)
            _toast(resp, "Не удалось распаковать .gz файл", "error")
            return resp

    db_cfg = config.database
    env = {
        "PGPASSWORD": db_cfg.db_password.get_secret_value(),
        "PATH": "/usr/bin:/bin:/usr/local/bin",
    }
    cmd = [
        "psql",
        "-h", db_cfg.db_host,
        "-p", str(db_cfg.db_port),
        "-U", db_cfg.db_user,
        "-d", db_cfg.db_name,
        "--no-password",
    ]

    try:
        result = subprocess.run(cmd, input=content, capture_output=True, env=env, timeout=300)
        if result.returncode != 0:
            err = result.stderr.decode(errors="replace")[:400]
            resp = Response(status_code=500)
            _toast(resp, f"Ошибка восстановления: {err[:100]}", "error")
            return resp
    except FileNotFoundError:
        resp = Response(status_code=500)
        _toast(resp, "psql не найден. Установи postgresql-client на сервере.", "error")
        return resp
    except subprocess.TimeoutExpired:
        resp = Response(status_code=500)
        _toast(resp, "Восстановление превысило таймаут (5 мин)", "error")
        return resp

    resp = Response(status_code=200)
    _toast(resp, "База данных успешно восстановлена")
    return resp


# ── PasarGuard / Marzban ──────────────────────────────────────────────────────

@router.get("/pasarguard", response_class=HTMLResponse)
async def pasarguard_page(request: Request, db: AsyncSession = Depends(get_db)):
    _require_auth(request)
    ctx = await _base_ctx(request, db, "pasarguard")
    ctx["bot_settings"] = await BotSettingsService(db).get_all()
    from app.services.pasarguard.pasarguard import PasarguardService
    try:
        ctx["marzban_stats"] = await PasarguardService().get_system_stats()
        ctx["marzban_ok"] = True
    except Exception:
        ctx["marzban_stats"] = None
        ctx["marzban_ok"] = False
    return templates.TemplateResponse("pasarguard.html", ctx)


@router.get("/pasarguard/users", response_class=HTMLResponse)
async def pg_users(request: Request):
    _require_auth(request)
    from app.services.pasarguard.pasarguard import PasarguardService
    try:
        data = await PasarguardService().get_users(limit=50)
        users = data.get("users", []) if isinstance(data, dict) else data
    except Exception as e:
        return HTMLResponse(f'<div style="color:#ef4444">Ошибка: {e}</div>')

    if not users:
        return HTMLResponse('<div style="color:#8892a4">Нет пользователей</div>')

    rows = ""
    for u in users:
        status = u.get("status", "")
        color = {"active": "#22c55e", "expired": "#ef4444", "disabled": "#eab308"}.get(status, "#8892a4")
        used = round((u.get("used_traffic", 0) or 0) / 1073741824, 2)
        limit = u.get("data_limit", 0) or 0
        limit_str = f"{round(limit/1073741824,1)} GB" if limit else "∞"
        rows += f"""<tr class="user-row">
          <td><code style="color:var(--accent)">{u.get('username','')}</code></td>
          <td><span style="color:{color};font-size:.75rem">{status}</span></td>
          <td style="font-size:.78rem;color:#8892a4">{used} / {limit_str}</td>
          <td style="font-size:.75rem;color:#8892a4">{u.get('expire','—') or '—'}</td>
        </tr>"""

    return HTMLResponse(f"""
    <div class="table-responsive">
    <table class="table mb-0">
      <thead><tr><th>Username</th><th>Статус</th><th>Трафик</th><th>Истекает</th></tr></thead>
      <tbody>{rows}</tbody>
    </table></div>""")


@router.get("/pasarguard/groups", response_class=HTMLResponse)
async def pg_groups(request: Request):
    _require_auth(request)
    from app.services.pasarguard.pasarguard import PasarguardService
    try:
        groups = await PasarguardService().get_groups()
    except Exception as e:
        return HTMLResponse(f'<div class="p-3" style="color:#ef4444">Ошибка: {e}</div>')

    if not groups:
        return HTMLResponse('<div class="p-3" style="color:#8892a4">Групп нет</div>')

    rows = ""
    for g in groups:
        disabled = "🔴" if g.get("is_disabled") else "🟢"
        inbounds = ", ".join(g.get("inbound_tags", []))
        rows += f"""<tr>
          <td><code style="color:var(--accent)">{g['id']}</code></td>
          <td class="text-white">{g['name']}</td>
          <td style="font-size:.75rem;color:#8892a4">{inbounds}</td>
          <td>{disabled}</td>
          <td style="color:#8892a4">{g.get('total_users',0)}</td>
        </tr>"""

    return HTMLResponse(f"""
    <div class="table-responsive">
    <table class="table mb-0">
      <thead><tr><th>ID</th><th>Название</th><th>Inbounds</th><th>Статус</th><th>Юзеров</th></tr></thead>
      <tbody>{rows}</tbody>
    </table></div>""")


@router.get("/pasarguard/nodes", response_class=HTMLResponse)
async def pg_nodes(request: Request):
    _require_auth(request)
    from app.services.pasarguard.pasarguard import PasarguardService
    try:
        data = await PasarguardService().get_nodes()
        nodes = data.get("nodes", []) if isinstance(data, dict) else data
    except Exception as e:
        return HTMLResponse(f'<div class="p-3" style="color:#ef4444">Ошибка: {e}</div>')

    if not nodes:
        return HTMLResponse('<div class="p-3" style="color:#8892a4">Нод нет</div>')

    rows = ""
    for n in nodes:
        status = n.get("status", "")
        color = {"connected": "#22c55e", "connecting": "#eab308", "error": "#ef4444"}.get(status, "#8892a4")
        rows += f"""<tr>
          <td><code style="color:var(--accent)">{n.get('id','')}</code></td>
          <td class="text-white">{n.get('name','')}</td>
          <td style="color:#8892a4;font-size:.8rem">{n.get('address','')}</td>
          <td><span style="color:{color};font-size:.75rem">{status}</span></td>
        </tr>"""

    return HTMLResponse(f"""
    <div class="table-responsive">
    <table class="table mb-0">
      <thead><tr><th>ID</th><th>Название</th><th>Адрес</th><th>Статус</th></tr></thead>
      <tbody>{rows}</tbody>
    </table></div>""")


# ── Keyboard Editor ───────────────────────────────────────────────────────────

# All available buttons definition
_ALL_BUTTONS = [
    {"id": "my_keys",       "label": "🔑 Мои подписки",    "callback": "my_keys"},
    {"id": "buy",           "label": "💳 Купить",           "callback": "buy"},
    {"id": "profile",       "label": "👤 Профиль",          "callback": "profile"},
    {"id": "balance",       "label": "💰 Баланс",           "callback": "balance"},
    {"id": "promo",         "label": "🎁 Промокод",         "callback": "enter_promo"},
    {"id": "support",       "label": "💬 Поддержка",        "callback": "support"},
    {"id": "connect",       "label": "📲 Как подключить",   "callback": "connect:menu"},
    {"id": "about",         "label": "ℹ️ О проекте",        "callback": "about"},
    {"id": "servers",       "label": "🌐 Серверы",          "callback": "servers"},
    {"id": "top_referrers", "label": "🏆 Топ рефереров",    "callback": "top_referrers"},
    {"id": "status",        "label": "📊 Статус",           "callback": "status_cmd"},
    {"id": "language",      "label": "🌐 Язык",             "callback": "language"},
    {"id": "trial",         "label": "🎁 Пробный период",   "callback": "trial"},
    {"id": "miniapp",       "label": "📱 Scr", "callback": "miniapp"},
]

_DEFAULT_LAYOUT = [
    [{"id": "my_keys",  "label": "🔑 Мои подписки",  "callback": "my_keys"}],
    [{"id": "buy",      "label": "💳 Купить",         "callback": "buy"}],
    [{"id": "balance",  "label": "💰 Баланс",         "callback": "balance"},
     {"id": "promo",    "label": "🎁 Промокод",       "callback": "enter_promo"}],
    [{"id": "connect",  "label": "📲 Как подключить", "callback": "connect:menu"},
     {"id": "about",    "label": "ℹ️ О проекте",      "callback": "about"}],
    [{"id": "profile",  "label": "👤 Профиль",        "callback": "profile"},
     {"id": "servers",  "label": "🌐 Серверы",        "callback": "servers"}],
    [{"id": "top_referrers", "label": "🏆 Топ рефереров", "callback": "top_referrers"}],
    [{"id": "support",  "label": "💬 Поддержка",      "callback": "support"}],
]


@router.get("/keyboard", response_class=HTMLResponse)
async def keyboard_editor(request: Request, db: AsyncSession = Depends(get_db)):
    _require_auth(request)
    ctx = await _base_ctx(request, db, "keyboard")
    ctx["bot_settings"] = await BotSettingsService(db).get_all()
    ctx["bot_info"] = await TelegramNotifyService().get_bot_info()
    ctx["all_buttons"] = _ALL_BUTTONS

    # Load saved layout
    import json as _json
    raw = await BotSettingsService(db).get("keyboard_layout")
    try:
        layout = _json.loads(raw) if raw else _DEFAULT_LAYOUT
    except Exception:
        layout = _DEFAULT_LAYOUT

    ctx["layout"] = layout
    ctx["welcome_text"] = await BotSettingsService(db).get("welcome_message") or "👋 Привет! Выбери действие:"

    # Used IDs
    used_ids = [b["id"] for row in layout for b in row]
    ctx["used_ids"] = used_ids

    return templates.TemplateResponse("keyboard_editor.html", ctx)


@router.post("/keyboard/save")
async def keyboard_save(request: Request, db: AsyncSession = Depends(get_db)):
    if not _check_session(request):
        return {"ok": False, "detail": "Unauthorized"}
    import json as _json
    body = await request.json()
    layout = body.get("layout", _DEFAULT_LAYOUT)
    await BotSettingsService(db).set("keyboard_layout", _json.dumps(layout))
    await db.commit()
    return {"ok": True}


@router.post("/keyboard/styles")
async def keyboard_styles(request: Request, db: AsyncSession = Depends(get_db)):
    if not _check_session(request):
        return {"ok": False, "detail": "Unauthorized"}
    body = await request.json()
    styles = body.get("styles", {})
    svc = BotSettingsService(db)
    for btn_id, style in styles.items():
        await svc.set(f"btn_style_{btn_id}", style)
    await db.commit()
    return {"ok": True}
