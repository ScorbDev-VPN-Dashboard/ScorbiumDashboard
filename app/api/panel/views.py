import gzip
import html
import io
import json
import re
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import cast, Numeric
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db
from app.core.config import config
from app.models.payment import PaymentStatus, PaymentType
from app.models.support import TicketPriority, TicketStatus
from app.schemas.user import UserDetail, UserRead
from app.services.bot_settings import BotSettingsService
from app.services.broadcast import BroadcastService
from app.services.payment import PaymentService
from app.services.plan import PlanService
from app.services.promo import PromoService
from app.services.referral import ReferralService
from app.services.support import SupportService
from app.services.telegram_notify import TelegramNotifyService
from app.services.user import UserService
from app.services.vpn_key import VpnKeyService
from app.utils.log import log
from app.utils.security import create_access_token, decode_access_token_full
from app.core.permissions import has_permission
from app.services.admin import AdminService
from app.models.admin import AdminRole
from app.services.export import ExportService
from app.services.notification import notification_manager

router = APIRouter()

_tpl_path = Path(__file__).resolve().parent.parent.parent / "templates"
templates = Jinja2Templates(directory=str(_tpl_path))

SESSION_COOKIE = "vpn_session"


def _toast(resp: Response, message: str, kind: str = "success") -> None:
    """Unicode-safe toast via HX-Trigger JSON header."""
    resp.headers["HX-Trigger"] = json.dumps(
        {"showToast": {"msg": message, "type": kind}}
    )


def _get_admin_info(request: Request) -> dict | None:
    """Extract admin info (sub + role) from session cookie. Returns None if invalid."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    return decode_access_token_full(token)


def _check_session(request: Request) -> bool:
    return _get_admin_info(request) is not None


def _require_auth(request: Request) -> dict:
    """Enforce authentication. Returns {"sub": str, "role": str}."""
    info = _get_admin_info(request)
    if info is None:
        is_htmx = request.headers.get("HX-Request") == "true"
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
    return info


def _require_permission(request: Request, permission: str) -> dict:
    """Enforce authentication and check permission. Returns admin info dict."""
    info = _require_auth(request)
    if not has_permission(info["role"], permission):
        is_htmx = request.headers.get("HX-Request") == "true"
        from fastapi import HTTPException

        if is_htmx:
            raise HTTPException(
                status_code=200,
                headers={
                    "HX-Trigger": json.dumps(
                        {"showToast": {"msg": "Недостаточно прав", "type": "error"}}
                    )
                },
            )
        raise HTTPException(status_code=403, detail="Insufficient permissions")


def _redirect(url: str):
    from fastapi import HTTPException

    raise HTTPException(status_code=302, headers={"Location": url})


async def _base_ctx(
    request: Request, db: AsyncSession, active: str, admin_info: dict | None = None
) -> dict:
    if admin_info is None:
        admin_info = _get_admin_info(request)
    open_tickets = await SupportService(db).count_open()
    pending_payments = await PaymentService(db).count_by_status(PaymentStatus.PENDING)
    role = admin_info["role"] if admin_info else ""
    settings = await BotSettingsService(db).get_all()
    custom_logo = settings.get("custom_logo", "")
    now = datetime.now(timezone.utc)
    moscow_tz = timezone(timedelta(hours=3))
    iran_tz = timezone(timedelta(hours=3, minutes=30))
    us_east = timezone(timedelta(hours=-5))
    return {
        "request": request,
        "active": active,
        "open_tickets": open_tickets,
        "pending_payments": pending_payments,
        "bot_username": None,
        "app_name": config.web.app_name,
        "app_version": config.web.app_version,
        "vpn_panel_type": "marzban",
        "admin_role": role,
        "admin_username": admin_info["sub"] if admin_info else "",
        "has_perm": has_permission,
        "current_time": now.strftime("%H:%M"),
        "current_date": now.strftime("%d %B %Y"),
        "time_moscow": now.astimezone(moscow_tz).strftime("%H:%M"),
        "time_tehran": now.astimezone(iran_tz).strftime("%H:%M"),
        "time_us": now.astimezone(us_east).strftime("%H:%M"),
        "csrf_token": request.cookies.get("csrf_token", ""),
        "custom_logo": custom_logo,
    }


# ── Mini App auto-login ───────────────────────────────────────────────────────
# One-time tokens stored in memory: {token: expiry_timestamp}
import time as _time

_miniapp_tokens: dict[str, float] = {}


@router.get("/miniapp-token")
async def get_miniapp_token(request: Request):
    _check_session(request) or _redirect("/panel/login")
    import secrets as _secrets

    token = _secrets.token_urlsafe(32)
    _miniapp_tokens[token] = _time.time() + 300
    return {"token": token}


@router.get("/miniapp-login")
async def miniapp_login(request: Request, token: str = ""):
    now = _time.time()
    expired = [k for k, v in _miniapp_tokens.items() if v < now]
    for k in expired:
        del _miniapp_tokens[k]

    if not token or token not in _miniapp_tokens or _miniapp_tokens[token] < now:
        return RedirectResponse(url="/panel/login", status_code=302)

    del _miniapp_tokens[token]
    session_token = create_access_token(subject=config.web.web_superadmin_username)
    resp = RedirectResponse(url="/panel/", status_code=302)
    resp.set_cookie(
        SESSION_COOKIE,
        session_token,
        httponly=True,
        samesite="none",
        secure=True,
        max_age=3600,
    )
    return resp


# ── Auth ──────────────────────────────────────────────────────────────────────


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, db: AsyncSession = Depends(get_db)):
    settings = await BotSettingsService(db).get_all()
    custom_logo = settings.get("custom_logo", "")
    return templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "error": None,
            "app_name": config.web.app_name,
            "app_version": config.web.app_version,
            "custom_logo": custom_logo,
        },
    )


@router.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    settings = await BotSettingsService(db).get_all()
    _error_ctx = {
        "request": request,
        "error": "Неверный логин или пароль",
        "app_name": config.web.app_name,
        "app_version": config.web.app_version,
        "custom_logo": settings.get("custom_logo", ""),
    }

    admin = None
    # Try DB-based admin auth first
    admin = await AdminService(db).authenticate(username, password)
    if admin:
        pass
    elif (
        username == config.web.web_superadmin_username
        and password == config.web.web_superadmin_password.get_secret_value()
    ):
        admin = await AdminService(db).get_by_username(username)
        if not admin:
            from app.models.admin import Admin, AdminRole
            import bcrypt as _bcrypt
            new_admin = Admin(
                username=username,
                password_hash=_bcrypt.hashpw(
                    config.web.web_superadmin_password.get_secret_value().encode(),
                    _bcrypt.gensalt()
                ).decode(),
                role=AdminRole.SUPERADMIN.value,
            )
            db.add(new_admin)
            await db.commit()
            await db.refresh(new_admin)
            admin = new_admin

    if not admin:
        return templates.TemplateResponse("login.html", _error_ctx)

    if not admin.is_active:
        return templates.TemplateResponse("login.html", {**_error_ctx, "error": "Аккаунт заблокирован"})

    if admin.totp_secret:
        preauth = create_access_token(subject=admin.username, role=admin.role, extra={"type": "preauth"})
        resp = RedirectResponse(url="/panel/2fa", status_code=302)
        resp.set_cookie("vpn_preauth", preauth, httponly=True, samesite="lax", max_age=300)
        return resp

    token = create_access_token(subject=admin.username, role=admin.role)
    resp = RedirectResponse(url="/panel/", status_code=302)
    resp.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax", max_age=86400)
    return resp


@router.get("/logout")
async def logout():
    resp = RedirectResponse(url="/panel/login", status_code=302)
    resp.delete_cookie(SESSION_COOKIE)
    resp.delete_cookie("vpn_preauth")
    return resp


@router.get("/2fa", response_class=HTMLResponse)
async def twofa_page(request: Request, db: AsyncSession = Depends(get_db)):
    preauth = request.cookies.get("vpn_preauth", "")
    has_session = bool(request.cookies.get(SESSION_COOKIE, ""))

    # If accessing with preauth cookie (during login), show login page
    if preauth and not has_session:
        try:
            payload = decode_access_token_full(preauth)
            if payload and payload.get("type") == "preauth":
                return templates.TemplateResponse(
                    "2fa_login.html",
                    {
                        "request": request,
                        "admin_username": payload.get("sub", ""),
                        "error": None,
                        "app_name": config.web.app_name,
                        "app_version": config.web.app_version,
                    },
                )
        except Exception:
            pass
        return RedirectResponse(url="/panel/login", status_code=302)

    # If accessing with full session, show settings page
    admin_info = _require_permission(request, "system")
    ctx = await _base_ctx(request, db, "2fa")
    ctx["admin"] = await AdminService(db).get_by_username(admin_info["username"])
    return templates.TemplateResponse("two_fa.html", ctx)


@router.post("/2fa-login")
async def twofa_login_submit(
    request: Request,
    code: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    preauth = request.cookies.get("vpn_preauth", "")
    if not preauth:
        return RedirectResponse(url="/panel/login", status_code=302)
    try:
        payload = decode_access_token_full(preauth)
        if not payload or payload.get("type") != "preauth":
            return RedirectResponse(url="/panel/login", status_code=302)
    except Exception:
        return RedirectResponse(url="/panel/login", status_code=302)

    import pyotp
    import hashlib
    import json

    admin = await AdminService(db).get_by_username(payload["sub"])
    if not admin or not admin.totp_secret:
        return RedirectResponse(url="/panel/login", status_code=302)

    totp = pyotp.TOTP(admin.totp_secret)
    if totp.verify(code):
        token = create_access_token(subject=admin.username, role=admin.role)
        resp = RedirectResponse(url="/panel/", status_code=302)
        resp.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax", max_age=86400)
        resp.delete_cookie("vpn_preauth")
        return resp

    # Try backup codes
    if admin.backup_codes:
        try:
            hashed_codes = json.loads(admin.backup_codes)
            code_hash = hashlib.sha256(code.upper().replace("-", "").strip().encode()).hexdigest()
            if code_hash in hashed_codes:
                hashed_codes.remove(code_hash)
                admin.backup_codes = json.dumps(hashed_codes)
                await db.commit()
                token = create_access_token(subject=admin.username, role=admin.role)
                resp = RedirectResponse(url="/panel/", status_code=302)
                resp.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax", max_age=86400)
                resp.delete_cookie("vpn_preauth")
                return resp
        except (json.JSONDecodeError, ValueError):
            pass

    return templates.TemplateResponse(
        "2fa_login.html",
        {
            "request": request,
            "admin_username": payload.get("sub", ""),
            "error": "Неверный код",
            "app_name": config.web.app_name,
            "app_version": config.web.app_version,
        },
    )


@router.get("/2fa/setup")
async def twofa_setup(request: Request, db: AsyncSession = Depends(get_db)):
    from fastapi.responses import JSONResponse
    import pyotp
    import qrcode
    import base64
    import io

    admin_info = _require_permission(request, "system")
    secret = pyotp.random_base32()
    totp = pyotp.TOTP(secret)
    uri = totp.provisioning_uri(
        name=admin_info["username"],
        issuer_name=config.web.app_name or "Scorbium"
    )
    qr = qrcode.QRCode(version=1, box_size=10, border=2)
    qr.add_data(uri)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    qr_b64 = base64.b64encode(buf.getvalue()).decode()
    return JSONResponse({"secret": secret, "qr_b64": qr_b64})


@router.post("/2fa/activate")
async def twofa_activate(request: Request, db: AsyncSession = Depends(get_db)):
    from fastapi.responses import JSONResponse
    import pyotp
    import secrets
    import hashlib
    import json
    from app.services.audit import AuditService

    admin_info = _require_permission(request, "system")
    body = await request.json()
    secret = body.get("secret", "")
    code = body.get("code", "")
    if len(code) != 6:
        return JSONResponse({"ok": False, "message": "Код должен быть 6 знаков"}, status_code=400)
    totp = pyotp.TOTP(secret)
    if not totp.verify(code):
        return JSONResponse({"ok": False, "message": "Неверный код"}, status_code=400)
    admin = await AdminService(db).get_by_username(admin_info["username"])
    if admin:
        admin.totp_secret = secret
        # Generate 8 backup codes
        raw_codes = [secrets.token_hex(4).upper() for _ in range(8)]
        hashed_codes = [hashlib.sha256(c.encode()).hexdigest() for c in raw_codes]
        admin.backup_codes = json.dumps(hashed_codes)
        await db.commit()
        await AuditService(db).log(admin.id, "2fa_enabled", "admin", admin.id)
        await db.commit()
        return JSONResponse({"ok": True, "message": "2FA активирована", "backup_codes": raw_codes})
    return JSONResponse({"ok": False, "message": "Админ не найден"}, status_code=404)


@router.post("/2fa/verify")
async def twofa_verify(request: Request, db: AsyncSession = Depends(get_db)):
    from fastapi.responses import JSONResponse
    import pyotp
    import hashlib
    import json

    preauth = request.cookies.get("vpn_preauth", "")
    if not preauth:
        return JSONResponse({"ok": False, "message": "Нет сессии"}, status_code=401)
    try:
        payload = decode_access_token_full(preauth)
        if not payload or payload.get("type") != "preauth":
            return JSONResponse({"ok": False, "message": "Нет сессии"}, status_code=401)
    except Exception:
        return JSONResponse({"ok": False, "message": "Нет сессии"}, status_code=401)

    body = await request.json()
    code = body.get("code", "")
    admin = await AdminService(db).get_by_username(payload["sub"])
    if not admin or not admin.totp_secret:
        return JSONResponse({"ok": False, "message": "2FA не настроена"}, status_code=400)

    used_backup = False
    totp = pyotp.TOTP(admin.totp_secret)
    if not totp.verify(code):
        # Try backup codes
        if admin.backup_codes:
            try:
                hashed_codes = json.loads(admin.backup_codes)
                code_hash = hashlib.sha256(code.upper().replace("-", "").strip().encode()).hexdigest()
                if code_hash in hashed_codes:
                    # Remove used code
                    hashed_codes.remove(code_hash)
                    admin.backup_codes = json.dumps(hashed_codes)
                    await db.commit()
                    used_backup = True
                else:
                    return JSONResponse({"ok": False, "message": "Неверный код"}, status_code=400)
            except (json.JSONDecodeError, ValueError):
                return JSONResponse({"ok": False, "message": "Неверный код"}, status_code=400)
        else:
            return JSONResponse({"ok": False, "message": "Неверный код"}, status_code=400)

    token = create_access_token(subject=admin.username, role=admin.role)
    resp = JSONResponse({"ok": True, "message": "OK", "backup": used_backup})
    resp.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax", max_age=86400)
    resp.delete_cookie("vpn_preauth")
    return resp


@router.post("/2fa/disable")
async def twofa_disable(request: Request, db: AsyncSession = Depends(get_db)):
    from fastapi.responses import JSONResponse
    import pyotp
    from app.services.audit import AuditService

    admin_info = _require_permission(request, "system")
    body = await request.json()
    code = body.get("code", "")
    admin = await AdminService(db).get_by_username(admin_info["username"])
    if not admin or not admin.totp_secret:
        return JSONResponse({"ok": False, "message": "2FA не включена"}, status_code=400)

    totp = pyotp.TOTP(admin.totp_secret)
    if not totp.verify(code):
        return JSONResponse({"ok": False, "message": "Неверный код"}, status_code=400)

    admin.totp_secret = None
    await db.commit()
    await AuditService(db).log(admin.id, "2fa_disabled", "admin", admin.id)
    await db.commit()
    return JSONResponse({"ok": True, "message": "2FA отключена"})


@router.get("/2fa/check")
async def twofa_check(request: Request, db: AsyncSession = Depends(get_db)):
    from fastapi.responses import JSONResponse
    admin_info = _require_permission(request, "system")
    admin = await AdminService(db).get_by_username(admin_info["username"])
    return JSONResponse({"enabled": bool(admin and admin.totp_secret)})


@router.post("/2fa/regenerate-backup")
async def twofa_regenerate_backup(request: Request, db: AsyncSession = Depends(get_db)):
    from fastapi.responses import JSONResponse
    import secrets
    import hashlib
    import json
    from app.services.audit import AuditService

    admin_info = _require_permission(request, "system")
    admin = await AdminService(db).get_by_username(admin_info["username"])
    if not admin or not admin.totp_secret:
        return JSONResponse({"ok": False, "message": "2FA не включена"}, status_code=400)

    raw_codes = [secrets.token_hex(4).upper() for _ in range(8)]
    hashed_codes = [hashlib.sha256(c.encode()).hexdigest() for c in raw_codes]
    admin.backup_codes = json.dumps(hashed_codes)
    await db.commit()
    await AuditService(db).log(admin.id, "2fa_backup_regenerated", "admin", admin.id)
    await db.commit()
    return JSONResponse({"ok": True, "message": "Резервные коды обновлены", "backup_codes": raw_codes})


# ── Dashboard ─────────────────────────────────────────────────────────────────


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    admin_info = _require_permission(request, "dashboard")
    ctx = await _base_ctx(request, db, "dashboard", admin_info)

    from datetime import datetime, timedelta, timezone

    from sqlalchemy import func, select

    from app.models.payment import Payment, PaymentStatus, PaymentType
    from app.models.user import User
    from app.models.vpn_key import VpnKey, VpnKeyStatus

    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    new_today_r = await db.execute(
        select(func.count()).select_from(User).where(User.created_at >= today_start)
    )
    new_today = new_today_r.scalar_one()

    rev_today_r = await db.execute(
        select(
            func.coalesce(func.sum(cast(Payment.amount, Numeric)), 0).label("total")
        ).where(
            Payment.status == PaymentStatus.SUCCEEDED.value,
            Payment.payment_type == PaymentType.SUBSCRIPTION.value,
            Payment.created_at >= today_start,
        )
    )
    rev_today_val = rev_today_r.scalar_one()
    rev_today = float(rev_today_val) if rev_today_val else 0.0

    expired_r = await db.execute(
        select(func.count())
        .select_from(VpnKey)
        .where(VpnKey.status == VpnKeyStatus.EXPIRED.value)
    )
    expired_count = expired_r.scalar_one()

    rev_week = []
    for i in range(6, -1, -1):
        day_start = today_start - timedelta(days=i)
        day_end = day_start + timedelta(days=1)
        r = await db.execute(
            select(
                func.coalesce(func.sum(cast(Payment.amount, Numeric)), 0).label("total")
            ).where(
                Payment.status == PaymentStatus.SUCCEEDED.value,
                Payment.payment_type == PaymentType.SUBSCRIPTION.value,
                Payment.created_at >= day_start,
                Payment.created_at < day_end,
            )
        )
        val = r.scalar_one()
        rev_week.append(float(val) if val else 0.0)

    ctx["stats"] = {
        "total_users": await UserService(db).count_all(),
        "active_subscriptions": await VpnKeyService(db).count_active(),
        "total_revenue": await PaymentService(db).total_revenue(),
        "total_topups": await PaymentService(db).total_topups(),
        "open_tickets": await SupportService(db).count_open(),
        "new_users_today": new_today,
        "revenue_today": rev_today,
        "expired_keys": expired_count,
        "pending_payments": await PaymentService(db).count_by_status(
            PaymentStatus.PENDING
        ),
    }
    ctx["rev_week"] = rev_week
    ctx["recent_users"] = await UserService(db).get_all(limit=8)
    ctx["recent_payments"] = await PaymentService(db).get_all(limit=8)

    from app.services.pasarguard.pasarguard import get_vpn_panel

    try:
        panel_stats = await get_vpn_panel().get_system_stats()
        ctx["marzban_stats"] = panel_stats
    except Exception:
        ctx["marzban_stats"] = None

    return templates.TemplateResponse("dashboard.html", ctx)


# ── Users ─────────────────────────────────────────────────────────────────────


@router.get("/users", response_class=HTMLResponse)
async def users_page(request: Request, db: AsyncSession = Depends(get_db)):
    _require_permission(request, "users.read")
    ctx = await _base_ctx(request, db, "users")
    raw = await UserService(db).get_all(limit=200)
    ctx["users"] = [_to_detail(u) for u in raw]
    ctx["plans"] = await PlanService(db).get_all(only_active=True)
    return templates.TemplateResponse("users.html", ctx)


@router.get("/users/search", response_class=HTMLResponse)
async def users_search(
    request: Request, q: str = "", db: AsyncSession = Depends(get_db)
):
    _require_permission(request, "users.read")
    raw = await UserService(db).get_all(limit=200)
    q = q.lower()
    filtered = [
        u
        for u in raw
        if q in (u.full_name or "").lower() or q in (u.username or "").lower()
    ]
    return templates.TemplateResponse(
        "partials/users_rows.html",
        {"request": request, "users": [_to_detail(u) for u in filtered]},
    )


@router.get("/users/{user_id}", response_class=HTMLResponse)
async def user_detail_page(
    user_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    admin_info = _require_permission(request, "users.read")
    ctx = await _base_ctx(request, db, "users", admin_info)
    from sqlalchemy import select

    from app.models.payment import Payment
    from app.models.vpn_key import VpnKey

    user = await UserService(db).get_by_id(user_id)
    if not user:
        resp = Response(status_code=404)
        _toast(resp, 'Пользователь не найден', 'error')
        return resp
    keys_result = await db.execute(

        select(VpnKey).where(VpnKey.user_id == user_id).order_by(VpnKey.id.desc())
    )
    pays_result = await db.execute(
        select(Payment)
        .where(Payment.user_id == user_id)
        .order_by(Payment.created_at.desc())
    )

    ctx["user"] = UserRead.model_validate(user)
    ctx["subscriptions"] = list(keys_result.scalars().all())
    ctx["payments"] = list(pays_result.scalars().all())
    ctx["plans"] = await PlanService(db).get_all(only_active=True)
    return templates.TemplateResponse("user_detail.html", ctx)


@router.post("/users/{user_id}/deduct-balance", response_class=HTMLResponse)
async def deduct_balance(
    user_id: int,
    request: Request,
    amount: Decimal = Form(...),
    db: AsyncSession = Depends(get_db),
):
    _require_permission(request, "users.write")
    if amount <= 0:
        resp = Response(status_code=400)
        _toast(resp, "Сумма должна быть больше нуля", "error")
        return resp
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
async def ban_user_view(
    user_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    _require_permission(request, "users.write")
    if user_id in config.telegram.telegram_admin_ids:
        resp = Response(status_code=400)
        _toast(resp, "Нельзя забанить администратора", "error")
        return resp
    user = await UserService(db).ban(user_id)
    if not user:
        resp = Response(status_code=404)
        _toast(resp, 'Пользователь не найден', 'error')
        return resp
    await db.commit()
    ban_msg = (
        await BotSettingsService(db).get("ban_message")
        or "🚫 Ваш аккаунт заблокирован."
    )
    await TelegramNotifyService().send_message(user_id, ban_msg)
    resp = templates.TemplateResponse(
        "partials/users_rows.html", {"request": request, "users": [_to_detail(user)]}
    )
    _toast(resp, "Пользователь заблокирован")
    return resp


@router.post("/users/{user_id}/unban", response_class=HTMLResponse)
async def unban_user_view(
    user_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    _require_permission(request, "users.write")
    user = await UserService(db).unban(user_id)
    if not user:
        resp = Response(status_code=404)
        _toast(resp, 'Пользователь не найден', 'error')
        return resp
    await db.commit()
    unban_msg = (
        await BotSettingsService(db).get("unban_message")
        or "✅ Ваш аккаунт разблокирован. Добро пожаловать обратно!"
    )
    await TelegramNotifyService().send_message(user_id, unban_msg)
    resp = templates.TemplateResponse(
        "partials/users_rows.html", {"request": request, "users": [_to_detail(user)]}
    )
    _toast(resp, "Пользователь разблокирован")
    return resp


@router.post("/users/{user_id}/gift-subscription", response_class=HTMLResponse)
async def gift_subscription(
    user_id: int,
    request: Request,
    plan_id: int = Form(...),
    db: AsyncSession = Depends(get_db),
):
    _require_permission(request, "users.write")
    plan = await PlanService(db).get_by_id(plan_id)
    if not plan:
        resp = Response(status_code=404)
        _toast(resp, 'Тариф не найден', 'error')
        return resp
    key = await VpnKeyService(db).provision(user_id=user_id, plan=plan)
    await db.commit()
    if key:
        await TelegramNotifyService().send_message(
            user_id,
            f"🎁 <b>Вам подарена подписка!</b>\n\nПлан: <b>{plan.name}</b> ({plan.duration_days} дней)\n\n"
            f"🔑 <b>Ссылка:</b>\n<code>{key.access_url}</code>",
        )
    resp = Response(status_code=200)
    _toast(
        resp,
        f"Подписка «{plan.name}» подарена"
        if key
        else "Ошибка создания ключа в Marzban",
        "success" if key else "error",
    )
    return resp


@router.post("/users/{user_id}/gift-days", response_class=HTMLResponse)
async def gift_days(
    user_id: int,
    request: Request,
    days: int = Form(...),
    name: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
):
    _require_permission(request, "users.write")
    if days < 1 or days > 3650:
        resp = Response(status_code=400)
        _toast(resp, "Количество дней должно быть от 1 до 3650", "error")
        return resp
    key_name = name.strip() if name else f"Подарок — {days} дн."
    key = await VpnKeyService(db).provision_days(user_id=user_id, days=days, name=key_name)
    await db.commit()
    if key:
        exp_str = key.expires_at.strftime("%d.%m.%Y") if key.expires_at else "—"
        await TelegramNotifyService().send_message(
            user_id,
            f"🎁 <b>Вам подарена подписка!</b>\n\n"
            f"Длительность: <b>{days} дней</b>\n"
            f"Действует до: <b>{exp_str}</b>\n\n"
            f"🔑 <b>Ссылка:</b>\n<code>{key.access_url}</code>",
        )
    resp = Response(status_code=200)
    _toast(
        resp,
        f"Подписка на {days} дней подарена" if key else "Ошибка создания ключа в Marzban",
        "success" if key else "error",
    )
    return resp


@router.post("/users/{user_id}/add-balance", response_class=HTMLResponse)
async def add_balance(
    user_id: int,
    request: Request,
    amount: Decimal = Form(...),
    db: AsyncSession = Depends(get_db),
):
    _require_permission(request, "users.write")
    if amount <= 0:
        resp = Response(status_code=400)
        _toast(resp, "Сумма должна быть больше нуля", "error")
        return resp
    await UserService(db).add_balance(user_id, amount)
    notify = TelegramNotifyService()
    await notify.send_message(user_id, f"💰 На ваш баланс зачислено <b>{amount} ₽</b>")
    resp = Response(status_code=200)
    _toast(resp, f"Баланс пополнен на {amount} ₽")
    return resp


@router.post("/users/bulk")
async def bulk_users_action(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    _require_permission(request, "users.write")
    from fastapi.responses import JSONResponse

    body = await request.json()
    action = body.get("action", "")
    user_ids = body.get("user_ids", [])

    if not user_ids or not isinstance(user_ids, list):
        return JSONResponse({"ok": False, "message": "Нет выбранных пользователей"}, status_code=400)

    if action == "ban":
        done = 0
        for uid in user_ids:
            if uid in config.telegram.telegram_admin_ids:
                continue
            user = await UserService(db).ban(uid)
            if user:
                done += 1
        await db.commit()
        return JSONResponse({"ok": True, "message": f"Забанено: {done}"})
    elif action == "unban":
        done = 0
        for uid in user_ids:
            user = await UserService(db).unban(uid)
            if user:
                done += 1
        await db.commit()
        return JSONResponse({"ok": True, "message": f"Разбанено: {done}"})
    else:
        return JSONResponse({"ok": False, "message": "Неизвестное действие"}, status_code=400)


@router.post("/users/bulk-balance")
async def bulk_balance_action(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    _require_permission(request, "users.write")
    from fastapi.responses import JSONResponse

    body = await request.json()
    user_ids = body.get("user_ids", [])
    amount = body.get("amount")

    if not user_ids or not amount:
        return JSONResponse({"ok": False, "message": "Нет данных"}, status_code=400)

    try:
        amount = Decimal(str(amount))
        if amount <= 0:
            raise ValueError
    except Exception:
        return JSONResponse({"ok": False, "message": "Неверная сумма"}, status_code=400)

    done = 0
    for uid in user_ids:
        user = await UserService(db).add_balance(uid, amount)
        if user:
            done += 1
            await TelegramNotifyService().send_message(
                uid, f"💰 На ваш баланс зачислено <b>{amount} ₽</b>"
            )
    await db.commit()
    return JSONResponse({"ok": True, "message": f"Пополнено {done} пользователей на {amount} ₽"})


@router.post("/users/bulk-gift")
async def bulk_gift_action(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    _require_permission(request, "users.write")
    from fastapi.responses import JSONResponse

    body = await request.json()
    user_ids = body.get("user_ids", [])
    plan_id = body.get("plan_id")

    if not user_ids or not plan_id:
        return JSONResponse({"ok": False, "message": "Нет данных"}, status_code=400)

    plan = await PlanService(db).get_by_id(int(plan_id))
    if not plan:
        return JSONResponse({"ok": False, "message": "Тариф не найден"}, status_code=404)

    done = 0
    for uid in user_ids:
        key = await VpnKeyService(db).provision(user_id=uid, plan=plan)
        if key:
            done += 1
            await TelegramNotifyService().send_message(
                uid,
                f"🎁 <b>Вам подарена подписка!</b>\n\nПлан: <b>{plan.name}</b> ({plan.duration_days} дней)\n\n"
                f"🔑 <b>Ссылка:</b>\n<code>{key.access_url}</code>",
            )
    await db.commit()
    return JSONResponse({"ok": True, "message": f"Подарено {done} подписок «{plan.name}»"})


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
        bg = "rgba(0,212,170,.2)" if msg.is_admin else "rgba(255,255,255,.05)"
        sender = (
            '<i class="bi bi-shield-check me-1" style="color:#00d4aa"></i>Поддержка'
            if msg.is_admin
            else f'<i class="bi bi-person me-1"></i>Пользователь {html.escape(str(msg.sender_id))}'
        )
        reply_btn = ""
        if not msg.is_admin:
            reply_btn = (
                f'<div class="mt-1 text-end">'
                f'<button class="btn btn-sm py-0 px-2" style="font-size:.65rem;color:#00d4aa;background:none;border:1px solid rgba(0,212,170,.3)" '
                f"onclick=\"document.querySelector('[name=text]').value=''\">✏️ Ответить</button>"
                f"</div>"
            )
        safe_text = html.escape(str(msg.text)) if msg.text else ""
        msgs_html += (
            f'<div class="mb-3 d-flex {align}">'
            f'<div style="max-width:80%;background:{bg};border-radius:10px;padding:.6rem .9rem;font-size:.85rem;color:#c8d0e0">'
            f'<div style="font-size:.7rem;color:#8892a4;margin-bottom:.3rem">{sender}</div>'
            f"{safe_text}{reply_btn}</div></div>"
        )
    return msgs_html


# ── Plans ─────────────────────────────────────────────────────────────────────


@router.get("/plans", response_class=HTMLResponse)
async def plans_page(request: Request, db: AsyncSession = Depends(get_db)):
    _require_permission(request, "plans")
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
    _require_permission(request, "plans")
    if price <= 0:
        resp = Response(status_code=400)
        _toast(resp, "Цена должна быть больше нуля", "error")
        return resp
    if duration_days < 1:
        resp = Response(status_code=400)
        _toast(resp, "Длительность должна быть минимум 1 день", "error")
        return resp
    import re

    slug = re.sub(r"[^a-z0-9]+", "_", name.lower().strip()).strip("_") or "plan"
    # ensure unique slug
    existing = await PlanService(db).get_by_slug(slug)
    if existing:
        import time

        slug = f"{slug}_{int(time.time()) % 10000}"
    await PlanService(db).create(
        name=name,
        slug=slug,
        duration_days=duration_days,
        price=price,
        description=description or None,
    )
    await db.commit()
    plans = await PlanService(db).get_all()
    resp = templates.TemplateResponse(
        "partials/plans_grid.html", {"request": request, "plans": plans}
    )
    _toast(resp, f"Тариф «{name}» создан")
    return resp


@router.post("/plans/{plan_id}/edit", response_class=HTMLResponse)
async def edit_plan_view(
    plan_id: int,
    request: Request,
    name: str = Form(...),
    price: Decimal = Form(...),
    duration_days: int = Form(...),
    description: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
):
    _require_permission(request, "plans")
    plan = await PlanService(db).update(
        plan_id,
        name=name,
        price=price,
        duration_days=duration_days,
        description=description or None,
    )
    await db.commit()
    plans = await PlanService(db).get_all()
    resp = templates.TemplateResponse(
        "partials/plans_grid.html", {"request": request, "plans": plans}
    )
    _toast(resp, f"Тариф «{plan.name if plan else plan_id}» обновлён")
    return resp


@router.post("/plans/{plan_id}/toggle", response_class=HTMLResponse)
async def toggle_plan_view(
    plan_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    _require_permission(request, "plans")
    plan = await PlanService(db).toggle_active(plan_id)
    if not plan:
        resp = Response(status_code=404)
        _toast(resp, 'Тариф не найден', 'error')
        return resp
    status_label = "active" if plan.is_active else "closed"
    status_text = "Активен" if plan.is_active else "Отключён"
    icon = "pause" if plan.is_active else "play"
    h = html.escape
    html_resp = f"""<div class="col-md-6 col-xl-4" id="plan-{plan.id}">
      <div class="card h-100 p-3">
        <div class="d-flex align-items-start justify-content-between mb-2">
          <div><div class="fw-bold text-white">{h(plan.name)}</div>
          <code style="font-size:.7rem;color:#8892a4">{h(plan.slug)}</code></div>
          <span class="badge badge-custom badge-{status_label}">{status_text}</span>
        </div>
        <div class="d-flex gap-3 mb-3" style="font-size:.8rem;color:#8892a4">
          <span><i class="bi bi-clock me-1"></i>{h(str(plan.duration_days))} дн.</span>
          <span><i class="bi bi-currency-ruble me-1"></i>{h(str(plan.price))} {h(plan.currency)}</span>
        </div>
        <div class="d-flex gap-2 mt-auto">
          <button class="btn btn-sm btn-outline-secondary"
            hx-post="/panel/plans/{plan.id}/toggle" hx-target="#plan-{plan.id}" hx-swap="outerHTML">
            <i class="bi bi-{icon}"></i>
          </button>
        </div>
      </div>
    </div>"""
    resp = HTMLResponse(html_resp)
    _toast(resp, f"Тариф {'включён' if plan.is_active else 'отключён'}")
    return resp


@router.delete("/plans/{plan_id}", response_class=HTMLResponse)
async def delete_plan_view(
    plan_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    _require_permission(request, "plans")
    await PlanService(db).delete(plan_id)
    resp = HTMLResponse("")
    _toast(resp, "Тариф удалён")
    return resp


@router.post("/plans/reorder")
async def reorder_plans(request: Request, db: AsyncSession = Depends(get_db)):
    _require_permission(request, "plans")
    from fastapi.responses import JSONResponse

    body = await request.json()
    order = body.get("order", [])
    for idx, plan_id_str in enumerate(order):
        try:
            plan_id = int(plan_id_str)
            await PlanService(db).update(plan_id, sort_order=idx)
        except (ValueError, Exception):
            pass
    await db.commit()
    return JSONResponse({"ok": True})


# ── Payments ──────────────────────────────────────────────────────────────────


@router.get("/payments", response_class=HTMLResponse)
async def payments_page(
    request: Request,
    status: Optional[str] = None,
    payment_type: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    _require_permission(request, "payments")
    ctx = await _base_ctx(request, db, "payments")
    from app.models.payment import PaymentType

    ps = PaymentStatus(status) if status else None
    pt = PaymentType(payment_type) if payment_type else None
    ctx["payments"] = await PaymentService(db).get_all(
        limit=200, status=ps, payment_type=pt
    )
    ctx["total_topups"] = await PaymentService(db).total_topups()
    ctx["current_status"] = status or ""
    ctx["current_type"] = payment_type or ""
    return templates.TemplateResponse("payments.html", ctx)


@router.get("/payments/stats", response_class=HTMLResponse)
async def payments_stats_page(request: Request, db: AsyncSession = Depends(get_db)):
    _require_permission(request, "payments")
    ctx = await _base_ctx(request, db, "payments_stats")
    from sqlalchemy import select, func, and_
    from datetime import timedelta
    from app.models.payment import Payment

    days = 30
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # Summary
    result = await db.execute(
        select(
            func.coalesce(func.sum(Payment.amount), 0).label('total'),
            func.count(Payment.id).label('count'),
            func.avg(Payment.amount).label('avg'),
        ).where(
            Payment.status == PaymentStatus.SUCCEEDED.value,
            Payment.created_at >= cutoff
        )
    )
    row = result.first()
    total_rev = float(row.total) if row else 0
    total_pay = row.count if row else 0
    avg_pay = float(row.avg) if row and row.avg else 0

    result = await db.execute(
        select(func.count(Payment.id)).where(
            Payment.created_at >= cutoff
        )
    )
    all_count = result.scalar() or 1

    result = await db.execute(
        select(func.count(Payment.id)).where(
            Payment.status == PaymentStatus.SUCCEEDED.value,
            Payment.created_at >= cutoff
        )
    )
    success_count = result.scalar() or 0

    ctx["stats"] = {
        "total_revenue": f"{total_rev:.2f}",
        "total_payments": total_pay,
        "avg_payment": f"{avg_pay:.2f}",
        "success_rate": round(success_count / all_count * 100) if all_count else 0,
    }

    # Provider breakdown
    provider_names = {
        'yookassa': 'YooKassa', 'yookassa_sbp': 'YooKassa СБП', 'cryptobot': 'CryptoBot',
        'telegram_stars': 'Telegram Stars', 'freekassa': 'FreeKassa', 'balance': 'Баланс', 'topup': 'Пополнение'
    }
    result = await db.execute(
        select(
            Payment.provider,
            func.count(Payment.id).label('cnt'),
            func.coalesce(func.sum(Payment.amount).filter(Payment.status == PaymentStatus.SUCCEEDED.value), 0).label('rev'),
            func.count(Payment.id).filter(Payment.status == PaymentStatus.SUCCEEDED.value).label('scnt'),
        ).where(
            Payment.created_at >= cutoff
        ).group_by(Payment.provider)
    )
    providers = []
    for r in result.all():
        providers.append({
            "provider": r.provider,
            "label": provider_names.get(r.provider, r.provider),
            "count": r.cnt,
            "revenue": float(r.rev),
            "success_count": r.scnt,
            "avg_amount": float(r.rev) / r.scnt if r.scnt else 0,
        })
    total_prov = sum(p["count"] for p in providers) or 1
    for p in providers:
        p["share"] = round(p["count"] / total_prov * 100)
    ctx["providers"] = sorted(providers, key=lambda x: x["revenue"], reverse=True)

    return templates.TemplateResponse("payments_stats.html", ctx)


@router.get("/payments/stats/json")
async def payments_stats_json(request: Request, days: int = 30, db: AsyncSession = Depends(get_db)):
    _require_permission(request, "payments")
    from fastapi.responses import JSONResponse
    from sqlalchemy import select, func
    from datetime import timedelta
    from app.models.payment import Payment

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # Summary
    result = await db.execute(
        select(
            func.coalesce(func.sum(Payment.amount), 0).label('total'),
            func.count(Payment.id).label('count'),
            func.avg(Payment.amount).label('avg'),
        ).where(
            Payment.status == PaymentStatus.SUCCEEDED.value,
            Payment.created_at >= cutoff
        )
    )
    row = result.first()
    total_rev = float(row.total) if row else 0
    total_pay = row.count if row else 0
    avg_pay = float(row.avg) if row and row.avg else 0

    result = await db.execute(select(func.count(Payment.id)).where(Payment.created_at >= cutoff))
    all_count = result.scalar() or 1
    result = await db.execute(select(func.count(Payment.id)).where(Payment.status == PaymentStatus.SUCCEEDED.value, Payment.created_at >= cutoff))
    success_count = result.scalar() or 0

    provider_names = {
        'yookassa': 'YooKassa', 'yookassa_sbp': 'YooKassa СБП', 'cryptobot': 'CryptoBot',
        'telegram_stars': 'Telegram Stars', 'freekassa': 'FreeKassa', 'balance': 'Баланс', 'topup': 'Пополнение'
    }
    provider_icons = {
        'yookassa': 'credit-card', 'yookassa_sbp': 'bank', 'cryptobot': 'currency-bitcoin',
        'telegram_stars': 'star', 'freekassa': 'lightning', 'balance': 'wallet2', 'topup': 'plus-circle'
    }
    result = await db.execute(
        select(
            Payment.provider,
            func.count(Payment.id).label('cnt'),
            func.coalesce(func.sum(Payment.amount).filter(Payment.status == PaymentStatus.SUCCEEDED.value), 0).label('rev'),
            func.count(Payment.id).filter(Payment.status == PaymentStatus.SUCCEEDED.value).label('scnt'),
        ).where(Payment.created_at >= cutoff).group_by(Payment.provider)
    )
    providers = []
    for r in result.all():
        providers.append({
            "provider": r.provider,
            "label": provider_names.get(r.provider, r.provider),
            "icon": provider_icons.get(r.provider, 'question-circle'),
            "count": r.cnt,
            "revenue": float(r.rev),
            "success_count": r.scnt,
            "avg_amount": float(r.rev) / r.scnt if r.scnt else 0,
        })
    total_prov = sum(p["count"] for p in providers) or 1
    for p in providers:
        p["share"] = round(p["count"] / total_prov * 100)

    # Daily revenue
    result = await db.execute(
        select(
            func.date_trunc('day', Payment.created_at).label('day'),
            func.coalesce(func.sum(Payment.amount), 0).label('amount'),
        ).where(
            Payment.status == PaymentStatus.SUCCEEDED.value,
            Payment.created_at >= cutoff
        ).group_by(func.date_trunc('day', Payment.created_at)).order_by(func.date_trunc('day', Payment.created_at))
    )
    daily = [{"date": str(r.day)[:10], "amount": float(r.amount)} for r in result.all()]

    return JSONResponse({
        "total_revenue": f"{total_rev:.2f}",
        "total_payments": total_pay,
        "avg_payment": f"{avg_pay:.2f}",
        "success_rate": round(success_count / all_count * 100) if all_count else 0,
        "providers": sorted(providers, key=lambda x: x["revenue"], reverse=True),
        "daily": daily,
    })


@router.post("/payments/{payment_id}/refund", response_class=HTMLResponse)
async def refund_payment_view(
    payment_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    _require_permission(request, "payments")
    payment = await PaymentService(db).refund(payment_id)
    if not payment:
        resp = Response(status_code=404)
        _toast(resp, 'Платёж не найден', 'error')
        return resp
    h = html.escape
    resp = HTMLResponse(f"""<tr>
      <td><code style="color:#00d4aa">#{payment.id}</code></td>
      <td><a href="/panel/users/{payment.user_id}" style="color:#00d4aa">{payment.user_id}</a></td>
      <td><span style="color:#8892a4;font-size:.8rem">{h(str(payment.provider))}</span></td>
      <td><b>{payment.amount}</b> {h(str(payment.currency))}</td>
      <td><span class="badge badge-custom badge-open">Возврат</span></td>
      <td style="color:#8892a4;font-size:.8rem">—</td><td></td></tr>""")
    _toast(resp, f"Возврат платежа #{payment_id} выполнен")
    return resp


# ── Subscriptions (VPN Keys) ──────────────────────────────────────────────────


@router.get("/subscriptions", response_class=HTMLResponse)
async def subscriptions_page(request: Request, db: AsyncSession = Depends(get_db)):
    _require_permission(request, "subscriptions.read")
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
    _require_permission(request, "subscriptions")
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
    _toast(
        resp,
        f"Подписка «{plan.name}» создана" if key else "Ошибка создания ключа в Marzban",
        "success" if key else "error",
    )
    return resp


@router.post("/subscriptions/create-days", response_class=HTMLResponse)
async def create_subscription_days(
    request: Request,
    user_id: int = Form(...),
    days: int = Form(...),
    name: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
):
    _require_permission(request, "subscriptions")
    if days < 1 or days > 3650:
        resp = Response(status_code=400)
        _toast(resp, "Количество дней должно быть от 1 до 3650", "error")
        return resp
    key_name = name.strip() if name else f"Подписка — {days} дн."
    key = await VpnKeyService(db).provision_days(user_id=user_id, days=days, name=key_name)
    await db.commit()
    if key:
        exp_str = key.expires_at.strftime("%d.%m.%Y") if key.expires_at else "—"
        await TelegramNotifyService().send_message(
            user_id,
            f"🎁 <b>Вам выдана подписка!</b>\n\n"
            f"Длительность: <b>{days} дней</b>\n"
            f"Действует до: <b>{exp_str}</b>\n\n"
            f"🔑 <b>Ссылка:</b>\n<code>{key.access_url}</code>",
        )
    resp = Response(status_code=200)
    _toast(
        resp,
        f"Подписка на {days} дней создана" if key else "Ошибка создания ключа в Marzban",
        "success" if key else "error",
    )
    return resp


@router.post("/subscriptions/{key_id}/extend", response_class=HTMLResponse)
async def extend_subscription(
    key_id: int,
    request: Request,
    days: int = Form(...),
    db: AsyncSession = Depends(get_db),
):
    _require_permission(request, "subscriptions")
    key = await VpnKeyService(db).extend(key_id, days)
    if not key:
        resp = Response(status_code=404)
        _toast(resp, "Подписка не найдена", "error")
        return resp
    await db.commit()
    exp_str = key.expires_at.strftime("%d.%m.%Y") if key.expires_at else "—"
    await TelegramNotifyService().send_message(
        key.user_id,
        f"📅 <b>Ваша подписка продлена на {days} дней!</b>\n\nДействует до: {exp_str}",
    )
    resp = Response(status_code=200)
    _toast(resp, f"Подписка #{key_id} продлена на {days} дней")
    return resp


@router.post("/subscriptions/{key_id}/cancel", response_class=HTMLResponse)
async def cancel_subscription(
    key_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    _require_permission(request, "subscriptions")
    key = await VpnKeyService(db).revoke(key_id)
    if not key:
        resp = Response(status_code=404)
        _toast(resp, "Подписка не найдена", "error")
        return resp
    await db.commit()
    await TelegramNotifyService().send_message(
        key.user_id, "❌ <b>Ваша подписка отменена.</b>"
    )
    resp = Response(status_code=200)
    _toast(resp, f"Подписка #{key_id} отменена")
    return resp


# ── Promo codes ───────────────────────────────────────────────────────────────


@router.get("/promos", response_class=HTMLResponse)
async def promos_page(request: Request, db: AsyncSession = Depends(get_db)):
    _require_permission(request, "promos")
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
    _require_permission(request, "promos")
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
        "partials/promos_table.html",
        {"request": request, "promos": promos, "plans": plans},
    )
    _toast(resp, f"Промокод {code.upper()} создан")
    return resp


@router.delete("/promos/{promo_id}", response_class=HTMLResponse)
async def delete_promo(
    promo_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    _require_permission(request, "promos")
    await PromoService(db).delete(promo_id)
    await db.commit()
    resp = HTMLResponse("")
    _toast(resp, "Промокод удалён")
    return resp


@router.post("/promos/{promo_id}/toggle", response_class=HTMLResponse)
async def toggle_promo(
    promo_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    _require_permission(request, "promos")
    promo = await PromoService(db).toggle_active(promo_id)
    await db.commit()
    if not promo:
        resp = Response(status_code=404)
        _toast(resp, 'Промокод не найден', 'error')
        return resp
    promos = await PromoService(db).get_all()
    plans = await PlanService(db).get_all(only_active=True)
    resp = templates.TemplateResponse(
        "partials/promos_table.html",
        {"request": request, "promos": promos, "plans": plans},
    )
    _toast(resp, "Статус промокода обновлён")
    return resp


# ── Referrals ─────────────────────────────────────────────────────────────────


@router.get("/referrals", response_class=HTMLResponse)
async def referrals_page(request: Request, db: AsyncSession = Depends(get_db)):
    _require_permission(request, "referrals")
    ctx = await _base_ctx(request, db, "referrals")
    ctx["stats"] = await ReferralService(db).get_stats()
    ctx["top"] = await ReferralService(db).get_top(limit=20)
    return templates.TemplateResponse("referrals.html", ctx)


# ── Support ───────────────────────────────────────────────────────────────────


@router.get("/support", response_class=HTMLResponse)
async def support_page(
    request: Request, status: Optional[str] = None, db: AsyncSession = Depends(get_db)
):
    _require_permission(request, "support")
    ctx = await _base_ctx(request, db, "support")
    ts = TicketStatus(status) if status else None
    ctx["tickets"] = await SupportService(db).get_all(status=ts, limit=100)
    ctx["ticket"] = None
    ctx["current_status"] = status or ""
    ctx["selected_id"] = None
    return templates.TemplateResponse("support.html", ctx)


@router.get("/support/{ticket_id}", response_class=HTMLResponse)
async def support_ticket(
    ticket_id: int,
    request: Request,
    status: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    _require_permission(request, "support")
    ctx = await _base_ctx(request, db, "support")
    ts = TicketStatus(status) if status else None
    ctx["tickets"] = await SupportService(db).get_all(status=ts, limit=100)
    ctx["ticket"] = await SupportService(db).get_by_id(ticket_id)
    ctx["current_status"] = status or ""
    ctx["selected_id"] = ticket_id
    return templates.TemplateResponse("support.html", ctx)


@router.post("/support/{ticket_id}/reply", response_class=HTMLResponse)
async def support_reply(
    ticket_id: int,
    request: Request,
    text: str = Form(...),
    notify_user: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
):
    _require_permission(request, "support")
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
async def support_close(
    ticket_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    _require_permission(request, "support")
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
async def support_status(
    ticket_id: int,
    request: Request,
    status: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    _require_permission(request, "support")
    await SupportService(db).set_status(ticket_id, TicketStatus(status))
    await db.commit()
    resp = Response(status_code=200)
    _toast(resp, "Статус обновлён")
    return resp


@router.patch("/support/{ticket_id}/priority")
async def support_priority(
    ticket_id: int,
    request: Request,
    priority: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    _require_permission(request, "support")
    await SupportService(db).set_priority(ticket_id, TicketPriority(priority))
    await db.commit()
    resp = Response(status_code=200)
    _toast(resp, "Приоритет обновлён")
    return resp


# ── VPN Keys ──────────────────────────────────────────────────────────────────


@router.get("/vpn", response_class=HTMLResponse)
async def vpn_page(request: Request, db: AsyncSession = Depends(get_db)):
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/panel/subscriptions", status_code=301)


@router.post("/vpn/{key_id}/revoke", response_class=HTMLResponse)
async def revoke_vpn_key(
    key_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    _require_permission(request, "vpn")
    key = await VpnKeyService(db).revoke(key_id)
    await db.commit()
    resp = Response(status_code=200)
    _toast(
        resp,
        f"Ключ #{key_id} отозван" if key else "Ключ не найден",
        "success" if key else "error",
    )
    return resp


@router.post("/vpn/{key_id}/extend", response_class=HTMLResponse)
async def extend_vpn_key(
    key_id: int,
    request: Request,
    days: int = Form(...),
    db: AsyncSession = Depends(get_db),
):
    _require_permission(request, "vpn")
    if days <= 0:
        resp = Response(status_code=400)
        _toast(resp, "Дней должно быть больше нуля", "error")
        return resp
    key = await VpnKeyService(db).extend(key_id, days)
    await db.commit()
    resp = Response(status_code=200)
    _toast(
        resp,
        f"Ключ #{key_id} продлён на {days} дн." if key else "Ключ не найден",
        "success" if key else "error",
    )
    return resp


@router.post("/vpn/{key_id}/delete", response_class=HTMLResponse)
async def delete_vpn_key(
    key_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    _require_permission(request, "vpn")
    key = await VpnKeyService(db).delete_from_marzban(key_id)
    await db.commit()
    resp = HTMLResponse("")
    _toast(
        resp,
        f"Ключ #{key_id} удалён из Marzban" if key else "Ключ не найден",
        "success" if key else "error",
    )
    return resp


@router.post("/vpn/sync", response_class=HTMLResponse)
async def sync_vpn_keys(request: Request, db: AsyncSession = Depends(get_db)):
    _require_permission(request, "vpn")
    result = await VpnKeyService(db).sync_from_marzban()
    await db.commit()
    resp = Response(status_code=200)
    _toast(resp, f"Синхронизировано: {result['synced']}, ошибок: {result['errors']}")
    return resp


@router.get("/broadcasts", response_class=HTMLResponse)
async def broadcasts_page(request: Request, db: AsyncSession = Depends(get_db)):
    _require_permission(request, "broadcasts")
    ctx = await _base_ctx(request, db, "broadcasts")
    ctx["broadcasts"] = await BroadcastService(db).get_all()
    return templates.TemplateResponse("broadcasts.html", ctx)


@router.post("/broadcasts", response_class=HTMLResponse)
async def create_broadcast_view(
    request: Request,
    title: str = Form(...),
    text: str = Form(...),
    target: str = Form("all"),
    parse_mode: str = Form("HTML"),
    db: AsyncSession = Depends(get_db),
):
    _require_permission(request, "broadcasts")
    await BroadcastService(db).create(
        title=title, text=text, target=target, parse_mode=parse_mode
    )
    resp = templates.TemplateResponse(
        "partials/broadcasts_list.html",
        {"request": request, "broadcasts": await BroadcastService(db).get_all()},
    )
    _toast(resp, "Черновик создан")
    return resp


@router.post("/broadcasts/{broadcast_id}/send", response_class=HTMLResponse)
async def send_broadcast_view(
    broadcast_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    _require_permission(request, "broadcasts")
    bc = await BroadcastService(db).send(broadcast_id)
    if not bc:
        return HTMLResponse("", status_code=400)
    resp = templates.TemplateResponse(
        "partials/broadcasts_list.html", {"request": request, "broadcasts": [bc]}
    )
    _toast(resp, f"Отправлено: {bc.sent_count}, ошибок: {bc.failed_count}")
    return resp


@router.get("/broadcasts/estimate")
async def broadcast_estimate(request: Request, target: str = "all", db: AsyncSession = Depends(get_db)):
    _require_permission(request, "broadcasts")
    from fastapi.responses import JSONResponse
    count = await BroadcastService(db).estimate_count(target)
    return JSONResponse({"count": count})


# ── Telegram ──────────────────────────────────────────────────────────────────


@router.get("/telegram", response_class=HTMLResponse)
async def telegram_page(request: Request, db: AsyncSession = Depends(get_db)):
    _require_permission(request, "system")
    ctx = await _base_ctx(request, db, "telegram")
    ctx["bot_info"] = await TelegramNotifyService().get_bot_info()
    ctx["admin_ids"] = config.telegram.telegram_admin_ids
    ctx["bot_settings"] = await BotSettingsService(db).get_all()

    import json as _json

    ctx["all_buttons"] = _ALL_BUTTONS
    ctx["default_layout"] = _DEFAULT_LAYOUT
    raw = await BotSettingsService(db).get("keyboard_layout")
    try:
        ctx["layout"] = _json.loads(raw) if raw else _DEFAULT_LAYOUT
    except Exception:
        ctx["layout"] = _DEFAULT_LAYOUT

    # Payment systems status
    svc = BotSettingsService(db)
    yk_shop = await svc.get("yookassa_shop_id_override") or ""
    yk_key_set = bool(await svc.get("yookassa_secret_key_override"))
    cb_token_set = bool((await svc.get("cryptobot_token") or "").strip())

    # Encryption status
    from app.services.encryption import get_encryption_key_info
    ctx["encryption_status"] = get_encryption_key_info()

    # Also check env-level yookassa
    yk_env_ok = bool(
        config.yookassa
        and config.yookassa.yookassa_shop_id
        and config.yookassa.yookassa_secret_key
    )

    yk_toggle = (await svc.get("ps_yookassa_enabled") or "0") == "1"
    cb_toggle = (await svc.get("ps_cryptobot_enabled") or "0") == "1"
    sbp_toggle = (await svc.get("ps_sbp_enabled") or "0") == "1"

    # FreeKassa
    fk_shop = (await svc.get("freekassa_shop_id") or "").strip()
    fk_api_set = bool((await svc.get("freekassa_api_key") or "").strip())
    fk_configured = bool(fk_shop and fk_api_set)
    fk_enabled = fk_configured and (await svc.get("ps_freekassa_enabled") or "0") == "1"

    # AiKassa
    ak_shop = (await svc.get("aikassa_shop_id") or "").strip()
    ak_token_set = bool((await svc.get("aikassa_token") or "").strip())
    ak_configured = bool(ak_shop and ak_token_set)
    ak_enabled = ak_configured and (await svc.get("ps_aikassa_enabled") or "0") == "1"

    from types import SimpleNamespace

    ctx["ps"] = SimpleNamespace(
        yookassa_enabled=(yk_env_ok or yk_key_set) and yk_toggle,
        yookassa_configured=yk_env_ok or yk_key_set,
        yookassa_shop_id=yk_shop
        or (
            str(config.yookassa.yookassa_shop_id)
            if config.yookassa and config.yookassa.yookassa_shop_id
            else ""
        ),
        yookassa_toggle=yk_toggle,
        cryptobot_enabled=cb_token_set and cb_toggle,
        cryptobot_configured=cb_token_set,
        cryptobot_toggle=cb_toggle,
        sbp_enabled=(yk_env_ok or yk_key_set) and sbp_toggle,
        sbp_toggle=sbp_toggle,
        freekassa_enabled=fk_enabled,
        freekassa_configured=fk_configured,
        freekassa_shop_id=fk_shop,
        freekassa_secret1_set=bool(await svc.get("freekassa_secret_word_1")),
        freekassa_secret2_set=bool(await svc.get("freekassa_secret_word_2")),
        aikassa_enabled=ak_enabled,
        aikassa_configured=ak_configured,
        aikassa_shop_id=ak_shop,
        stars_rate=float(await svc.get("stars_rate") or "1.5"),
    )

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


# ── Payment Systems ───────────────────────────────────────────────────────────

_ALLOWED_PS_KEYS = frozenset(
    ["yookassa_shop_id_override", "yookassa_secret_key_override", "cryptobot_token"]
)


@router.post("/telegram/payment-systems/yookassa")
async def ps_save_yookassa(request: Request, db: AsyncSession = Depends(get_db)):
    """Сохраняет настройки ЮКассы в bot_settings. Все данные через ORM — SQL-инъекции невозможны."""
    _require_permission(request, "system")
    from fastapi.responses import JSONResponse
    import re

    form = await request.form()
    shop_id_raw = str(form.get("yookassa_shop_id", "")).strip()
    secret_key_raw = str(form.get("yookassa_secret_key", "")).strip()

    svc = BotSettingsService(db)

    # Валидация shop_id
    if shop_id_raw:
        if not re.fullmatch(r"\d{5,8}", shop_id_raw):
            return JSONResponse(
                {"ok": False, "message": "Shop ID: 5-8 цифр"}, status_code=400
            )
        await svc.set("yookassa_shop_id_override", shop_id_raw)

    # Валидация secret_key
    if secret_key_raw:
        if len(secret_key_raw) < 10:
            return JSONResponse(
                {
                    "ok": False,
                    "message": "Secret Key слишком короткий (мин. 10 символов)",
                },
                status_code=400,
            )
        if not re.fullmatch(r"[A-Za-z0-9_\-]+", secret_key_raw):
            return JSONResponse(
                {"ok": False, "message": "Secret Key содержит недопустимые символы"},
                status_code=400,
            )
        await svc.set("yookassa_secret_key_override", secret_key_raw)

    await db.commit()

    # Проверяем итоговое состояние
    saved_shop = await svc.get("yookassa_shop_id_override") or ""
    saved_key = bool(await svc.get("yookassa_secret_key_override"))
    enabled = bool(saved_shop and saved_key)

    return JSONResponse({"ok": True, "message": "ЮКасса сохранена", "enabled": enabled})


@router.post("/telegram/payment-systems/yookassa/test")
async def ps_test_yookassa(request: Request, db: AsyncSession = Depends(get_db)):
    """Проверяет подключение к ЮКассе."""
    _require_permission(request, "system")
    from fastapi.responses import JSONResponse

    svc = BotSettingsService(db)
    shop_id_str = await svc.get("yookassa_shop_id_override") or ""
    secret_key = await svc.get("yookassa_secret_key_override") or ""

    # Fallback to env config
    if not shop_id_str or not secret_key:
        if (
            config.yookassa
            and config.yookassa.yookassa_shop_id
            and config.yookassa.yookassa_secret_key
        ):
            shop_id_str = str(config.yookassa.yookassa_shop_id)
            secret_key = config.yookassa.yookassa_secret_key.get_secret_value()

    if not shop_id_str or not secret_key:
        return JSONResponse(
            {"ok": False, "message": "ЮКасса не настроена"}, status_code=400
        )

    try:
        import yookassa as _yk

        _yk.Configuration.account_id = int(shop_id_str)
        _yk.Configuration.secret_key = secret_key
        # Делаем тестовый запрос — получаем список платежей (пустой список = успех)
        import httpx

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://api.yookassa.ru/v3/payments",
                params={"limit": 1},
                auth=(shop_id_str, secret_key),
            )
        if resp.status_code in (200, 401):
            if resp.status_code == 401:
                return JSONResponse(
                    {"ok": False, "message": "Неверные учётные данные ЮКассы"},
                    status_code=400,
                )
            return JSONResponse(
                {
                    "ok": True,
                    "message": f"✅ ЮКасса подключена (shop_id: {shop_id_str})",
                }
            )
        return JSONResponse(
            {"ok": False, "message": f"Ошибка API: {resp.status_code}"}, status_code=400
        )
    except Exception as e:
        log.error("YooKassa test error: %s", e)
        return JSONResponse(
            {"ok": False, "message": "Ошибка подключения к ЮКассе"}, status_code=400
        )


@router.post("/telegram/payment-systems/cryptobot")
async def ps_save_cryptobot(request: Request, db: AsyncSession = Depends(get_db)):
    """Сохраняет токен CryptoBot в bot_settings."""
    _require_permission(request, "system")
    from fastapi.responses import JSONResponse
    import re

    form = await request.form()
    token_raw = str(form.get("cryptobot_token", "")).strip()

    if not token_raw:
        return JSONResponse(
            {"ok": False, "message": "Токен не указан"}, status_code=400
        )

    if not re.fullmatch(r"[\d]+:[A-Za-z0-9_\-]+", token_raw):
        return JSONResponse(
            {
                "ok": False,
                "message": "Неверный формат токена (ожидается: 12345:AAA...)",
            },
            status_code=400,
        )

    svc = BotSettingsService(db)
    await svc.set("cryptobot_token", token_raw)
    await db.commit()

    return JSONResponse(
        {"ok": True, "message": "CryptoBot токен сохранён", "enabled": True}
    )


@router.post("/telegram/payment-systems/cryptobot/test")
async def ps_test_cryptobot(request: Request, db: AsyncSession = Depends(get_db)):
    """Проверяет подключение к CryptoBot."""
    _require_permission(request, "system")
    from fastapi.responses import JSONResponse

    svc = BotSettingsService(db)
    token = (await svc.get("cryptobot_token") or "").strip()

    if not token:
        return JSONResponse(
            {"ok": False, "message": "CryptoBot не настроен"}, status_code=400
        )

    try:
        from app.services.cryptobot import CryptoBotService

        crypto = CryptoBotService(token)
        info = await crypto.get_me()
        if info:
            name = info.get("name", "")
            app_id = info.get("app_id", "")
            return JSONResponse(
                {
                    "ok": True,
                    "message": f"✅ CryptoBot подключён: {name} (ID: {app_id})",
                }
            )
        return JSONResponse(
            {"ok": False, "message": "Не удалось получить данные от CryptoBot"},
            status_code=400,
        )
    except Exception as e:
        log.error("CryptoBot test error: %s", e)
        return JSONResponse(
            {"ok": False, "message": "Ошибка подключения к CryptoBot"}, status_code=400
        )


@router.post("/telegram/payment-systems/toggle")
async def ps_toggle(request: Request, db: AsyncSession = Depends(get_db)):
    """Включает/отключает платёжную систему. Хранит флаг в bot_settings."""
    _require_permission(request, "system")
    from fastapi.responses import JSONResponse

    _ALLOWED_TOGGLE_KEYS = frozenset(
        [
            "ps_yookassa_enabled",
            "ps_cryptobot_enabled",
            "ps_freekassa_enabled",
            "ps_aikassa_enabled",
            "ps_stars_enabled",
            "ps_sbp_enabled",
        ]
    )

    form = await request.form()
    key = str(form.get("key", "")).strip()
    enabled = str(form.get("enabled", "0")).strip()

    if key not in _ALLOWED_TOGGLE_KEYS:
        return JSONResponse(
            {"ok": False, "message": "Недопустимый ключ"}, status_code=400
        )
    if enabled not in ("0", "1"):
        return JSONResponse(
            {"ok": False, "message": "Недопустимое значение"}, status_code=400
        )

    svc = BotSettingsService(db)
    await svc.set(key, enabled)
    await db.commit()
    state = "включена" if enabled == "1" else "отключена"
    return JSONResponse(
        {"ok": True, "message": f"Система {state}", "enabled": enabled == "1"}
    )


@router.post("/telegram/payment-systems/freekassa")
async def ps_save_freekassa(request: Request, db: AsyncSession = Depends(get_db)):
    """Сохраняет настройки FreeKassa в bot_settings через ORM (без SQL-инъекций)."""
    _require_permission(request, "system")
    from fastapi.responses import JSONResponse
    import re

    form = await request.form()
    shop_id_raw = str(form.get("freekassa_shop_id", "")).strip()
    api_key_raw = str(form.get("freekassa_api_key", "")).strip()
    secret1_raw = str(form.get("freekassa_secret_word_1", "")).strip()
    secret2_raw = str(form.get("freekassa_secret_word_2", "")).strip()

    svc = BotSettingsService(db)

    if shop_id_raw:
        if not re.fullmatch(r"\d{1,10}", shop_id_raw):
            return JSONResponse(
                {"ok": False, "message": "Shop ID: только цифры (до 10 знаков)"},
                status_code=400,
            )
        await svc.set("freekassa_shop_id", shop_id_raw)

    if api_key_raw:
        if len(api_key_raw) < 8:
            return JSONResponse(
                {"ok": False, "message": "API Key слишком короткий (мин. 8 символов)"},
                status_code=400,
            )
        if not re.fullmatch(r"[A-Za-z0-9_\-]+", api_key_raw):
            return JSONResponse(
                {"ok": False, "message": "API Key содержит недопустимые символы"},
                status_code=400,
            )
        await svc.set("freekassa_api_key", api_key_raw)

    if secret1_raw:
        await svc.set("freekassa_secret_word_1", secret1_raw)
    if secret2_raw:
        await svc.set("freekassa_secret_word_2", secret2_raw)

    await db.commit()

    saved_shop = await svc.get("freekassa_shop_id") or ""
    saved_key = bool(await svc.get("freekassa_api_key"))
    configured = bool(saved_shop and saved_key)

    return JSONResponse(
        {"ok": True, "message": "FreeKassa сохранена", "configured": configured}
    )


@router.post("/telegram/payment-systems/freekassa/test")
async def ps_test_freekassa(request: Request, db: AsyncSession = Depends(get_db)):
    """Проверяет подключение к FreeKassa через API баланса."""
    _require_permission(request, "system")
    from fastapi.responses import JSONResponse

    svc = BotSettingsService(db)
    shop_id = (await svc.get("freekassa_shop_id") or "").strip()
    api_key = (await svc.get("freekassa_api_key") or "").strip()

    if not shop_id or not api_key:
        return JSONResponse(
            {"ok": False, "message": "FreeKassa не настроена"}, status_code=400
        )

    try:
        from app.services.freekassa import FreeKassaService

        fk = FreeKassaService(shop_id, api_key)
        data = await fk.get_balance()
        if data is None:
            return JSONResponse(
                {"ok": False, "message": "Нет ответа от FreeKassa"}, status_code=400
            )
        if data.get("type") == "error":
            msg = data.get("message", "Ошибка API")
            return JSONResponse(
                {"ok": False, "message": f"FreeKassa: {msg}"}, status_code=400
            )
        balance = data.get("balance", [])
        rub = next(
            (b.get("value", 0) for b in balance if b.get("currency") == "RUB"), 0
        )
        return JSONResponse(
            {"ok": True, "message": f"✅ FreeKassa подключена. Баланс: {rub} ₽"}
        )
    except Exception as e:
        log.error("FreeKassa test error: %s", e)
        return JSONResponse(
            {"ok": False, "message": "Ошибка подключения к FreeKassa"}, status_code=400
        )


@router.post("/telegram/payment-systems/aikassa")
async def ps_save_aikassa(request: Request, db: AsyncSession = Depends(get_db)):
    """Сохраняет настройки AiKassa в bot_settings через ORM."""
    _require_permission(request, "system")
    from fastapi.responses import JSONResponse
    import re

    form = await request.form()
    shop_id_raw = str(form.get("aikassa_shop_id", "")).strip()
    token_raw = str(form.get("aikassa_token", "")).strip()

    svc = BotSettingsService(db)

    if shop_id_raw:
        if not re.fullmatch(r"[A-Za-z0-9_\-]{1,64}", shop_id_raw):
            return JSONResponse(
                {"ok": False, "message": "Shop ID содержит недопустимые символы"},
                status_code=400,
            )
        await svc.set("aikassa_shop_id", shop_id_raw)

    if token_raw:
        if len(token_raw) < 10:
            return JSONResponse(
                {"ok": False, "message": "Токен слишком короткий (мин. 10 символов)"},
                status_code=400,
            )
        if not re.fullmatch(r"[A-Za-z0-9_\-\.]+", token_raw):
            return JSONResponse(
                {"ok": False, "message": "Токен содержит недопустимые символы"},
                status_code=400,
            )
        await svc.set("aikassa_token", token_raw)

    await db.commit()

    saved_shop = await svc.get("aikassa_shop_id") or ""
    saved_token = bool(await svc.get("aikassa_token"))
    configured = bool(saved_shop and saved_token)

    return JSONResponse(
        {"ok": True, "message": "AiKassa сохранена", "configured": configured}
    )


@router.post("/telegram/payment-systems/aikassa/test")
async def ps_test_aikassa(request: Request, db: AsyncSession = Depends(get_db)):
    """Проверяет подключение к AiKassa."""
    _require_permission(request, "system")
    from fastapi.responses import JSONResponse

    svc = BotSettingsService(db)
    shop_id = (await svc.get("aikassa_shop_id") or "").strip()
    token = (await svc.get("aikassa_token") or "").strip()

    if not shop_id or not token:
        return JSONResponse(
            {"ok": False, "message": "AiKassa не настроена"}, status_code=400
        )

    try:
        from app.services.aikassa import AiKassaService

        ak = AiKassaService(shop_id, token)
        data = await ak.get_shop_info()
        if data is None:
            return JSONResponse(
                {"ok": False, "message": "Нет ответа от AiKassa"}, status_code=400
            )
        if isinstance(data, dict) and data.get("error"):
            return JSONResponse(
                {"ok": False, "message": f"AiKassa: {data['error']}"}, status_code=400
            )
        name = data.get("name", shop_id) if isinstance(data, dict) else shop_id
        return JSONResponse({"ok": True, "message": f"✅ AiKassa подключена: {name}"})
    except Exception as e:
        log.error("AiKassa test error: %s", e)
        return JSONResponse(
            {"ok": False, "message": "Ошибка подключения к AiKassa"}, status_code=400
        )


@router.post("/telegram/payment-systems/stars-rate")
async def ps_save_stars_rate(request: Request, db: AsyncSession = Depends(get_db)):
    """Сохраняет курс Telegram Stars (1 Star = X рублей)."""
    _require_permission(request, "system")
    from fastapi.responses import JSONResponse
    import re

    form = await request.form()
    rate_raw = str(form.get("stars_rate", "")).strip()

    if not rate_raw:
        return JSONResponse({"ok": False, "message": "Курс не указан"}, status_code=400)
    if not re.fullmatch(r"\d+(\.\d+)?", rate_raw):
        return JSONResponse(
            {"ok": False, "message": "Некорректное значение курса"}, status_code=400
        )

    rate = float(rate_raw)
    if rate <= 0 or rate > 1000:
        return JSONResponse(
            {"ok": False, "message": "Курс должен быть от 0.1 до 1000"}, status_code=400
        )

    svc = BotSettingsService(db)
    await svc.set("stars_rate", str(rate))
    await db.commit()
    return JSONResponse({"ok": True, "message": f"✅ Курс сохранён: 1 Star = {rate} ₽"})


@router.post("/telegram/test-marzban", response_class=HTMLResponse)
async def test_marzban(request: Request):
    _require_permission(request, "system")
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
            ("bi-wifi", "rgba(34,197,94,.1)", "#22c55e", "Онлайн", users_active),
            ("bi-people", "rgba(0,212,170,.1)", "#0ea5e9", "Всего юзеров", total_user),
            (
                "bi-arrow-down-circle",
                "rgba(59,130,246,.1)",
                "#3b82f6",
                "Входящий",
                f"{incoming} GB",
            ),
            (
                "bi-arrow-up-circle",
                "rgba(239,68,68,.1)",
                "#ef4444",
                "Исходящий",
                f"{outgoing} GB",
            ),
            ("bi-memory", "rgba(234,179,8,.1)", "#eab308", "RAM", f"{ram_mb} MB"),
            ("bi-cpu", "rgba(0,212,170,.1)", "#0ea5e9", "CPU", f"{cpu}%"),
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
        safe_err = html.escape(str(e)[:80])
        html = f"""<div class="d-flex align-items-center gap-2" style="color:#ef4444;font-size:.85rem">
          <i class="bi bi-x-circle-fill"></i><span>Ошибка: {safe_err}</span>
        </div>"""
        resp = HTMLResponse(html)
        _toast(resp, "Нет подключения к Marzban", "error")
    return resp


@router.get("/telegram/groups", response_class=HTMLResponse)
async def get_marzban_groups(request: Request):
    """HTMX: возвращает список групп из Marzban для отображения чекбоксов."""
    _require_permission(request, "system")
    from app.services.pasarguard.pasarguard import PasarguardService

    groups = await PasarguardService().get_groups()
    if not groups:
        return HTMLResponse(
            '<div style="color:#ef4444;font-size:.8rem"><i class="bi bi-x-circle me-1"></i>Не удалось загрузить группы</div>'
        )

    html = ""
    for g in groups:
        disabled = " (отключена)" if g.get("is_disabled") else ""
        inbounds = ", ".join(g.get("inbound_tags", []))
        group_name = html.escape(str(g.get("name", "")))
        html += (
            f'<div class="form-check mb-2">'
            f'<input class="form-check-input" type="checkbox" name="group_id" value="{g["id"]}" id="grp{g["id"]}">'
            f'<label class="form-check-label" for="grp{g["id"]}" style="color:#c8d0e0;font-size:.85rem">'
            f"<b>{group_name}</b>{disabled}"
            f'<span style="color:#8892a4;font-size:.75rem;display:block">{html.escape(inbounds)} · {g.get("total_users", 0)} юзеров</span>'
            f"</label></div>"
        )
    return HTMLResponse(html)



@router.post("/telegram/groups", response_class=HTMLResponse)
async def save_marzban_groups(request: Request, db: AsyncSession = Depends(get_db)):
    """Сохраняет выбранные group_ids в bot_settings."""
    _require_permission(request, "system")
    import json as _json

    form = await request.form()
    group_ids = [int(v) for v in form.getlist("group_id") if str(v).isdigit()]
    await BotSettingsService(db).set("vpn_group_ids", _json.dumps(group_ids))
    await db.commit()
    resp = Response(status_code=200)
    _toast(resp, f"Группы сохранены: {group_ids if group_ids else 'все (без фильтра)'}")
    return resp


_MAX_UPLOAD = 10 * 1024 * 1024  # 10MB


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
    _require_permission(request, "system")
    import httpx
    from fastapi.responses import JSONResponse

    token = config.telegram.telegram_bot_token.get_secret_value()
    admin_ids = config.telegram.telegram_admin_ids
    if not admin_ids:
        return JSONResponse({"detail": "Нет admin_ids в конфиге"}, status_code=400)

    chat_id = admin_ids[0]
    content = await photo.read()
    if len(content) > _MAX_UPLOAD:
        return JSONResponse({"detail": "Файл слишком большой (макс. 10MB)"}, status_code=413)
    filename = photo.filename or "photo.jpg"

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"https://api.telegram.org/bot{token}/sendPhoto",
                data={"chat_id": chat_id, "caption": f"📎 Фото для раздела: {key}"},
                files={
                    "photo": (filename, content, photo.content_type or "image/jpeg")
                },
            )
        result = resp.json()
        if not result.get("ok"):
            return JSONResponse(
                {"detail": result.get("description", "Ошибка Telegram")},
                status_code=400,
            )

        photos = result["result"]["photo"]
        file_id = photos[-1]["file_id"]

        await BotSettingsService(db).set(key, file_id)
        await db.commit()
        return JSONResponse({"file_id": file_id})

    except Exception as e:
        log.error("Photo upload failed: %s", e)
        return JSONResponse({"detail": "Ошибка загрузки фото"}, status_code=500)


@router.post("/telegram/miniapp", response_class=HTMLResponse)
async def save_miniapp(
    request: Request,
    panel_url: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    """Сохраняет URL панели для кнопки Mini App в /admin команде."""
    _require_permission(request, "system")
    panel_url = panel_url.strip().rstrip("/")
    await BotSettingsService(db).set("panel_url", panel_url)
    await db.commit()
    resp = Response(status_code=200)
    _toast(resp, "URL панели сохранён. Кнопка появится в /admin для администраторов.")
    return resp


@router.post("/telegram/photo/clear")
async def clear_photo(
    request: Request,
    key: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    _require_permission(request, "system")
    await BotSettingsService(db).set(key, "")
    await db.commit()
    resp = Response(status_code=200)
    _toast(resp, "Фото удалено")
    return resp


@router.post("/logo/upload")
async def upload_logo(
    request: Request,
    logo: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Upload custom panel logo. Saves to static/img/custom-logo."""
    _require_permission(request, "system")
    import shutil
    from pathlib import Path

    content = await logo.read()
    if len(content) > 2 * 1024 * 1024:
        resp = Response(status_code=413)
        _toast(resp, "Файл слишком большой (макс. 2MB)", "error")
        return resp

    ext = Path(logo.filename or "").suffix.lower()
    if ext not in (".png", ".jpg", ".jpeg", ".webp", ".svg", ".gif"):
        resp = Response(status_code=400)
        _toast(resp, "Неподдерживаемый формат. Используйте PNG, JPG, WebP, SVG или GIF", "error")
        return resp

    static_path = Path(__file__).resolve().parent.parent.parent / "static" / "img"
    static_path.mkdir(parents=True, exist_ok=True)
    target = static_path / "custom-logo"

    for f in static_path.glob("custom-logo.*"):
        f.unlink()

    target = target.with_suffix(ext)
    target.write_bytes(content)

    await BotSettingsService(db).set("custom_logo", f"/static/img/custom-logo{ext}")
    await db.commit()

    resp = Response(status_code=200)
    _toast(resp, "Логотип загружен")
    return resp


@router.post("/logo/clear")
async def clear_logo(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Remove custom panel logo."""
    _require_permission(request, "system")
    import glob as _glob
    from pathlib import Path

    static_path = Path(__file__).resolve().parent.parent.parent / "static" / "img"
    for f in static_path.glob("custom-logo.*"):
        f.unlink()

    await BotSettingsService(db).set("custom_logo", "")
    await db.commit()

    resp = Response(status_code=200)
    _toast(resp, "Логотип удалён. Используется значок по умолчанию.")
    return resp


@router.post("/database/clear")
async def clear_database(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Clear all user data (users, keys, payments, tickets) while preserving settings and admins."""
    _require_permission(request, "system")
    from fastapi.responses import JSONResponse
    from sqlalchemy import text

    try:
        await db.execute(text("DELETE FROM ticket_messages"))
        await db.execute(text("DELETE FROM referrals"))
        await db.execute(text("DELETE FROM support_tickets"))
        await db.execute(text("DELETE FROM payments"))
        await db.execute(text("DELETE FROM vpn_keys"))
        await db.execute(text("DELETE FROM users"))
        await db.commit()
        return JSONResponse({"ok": True, "message": "База данных успешно очищена"})
    except Exception as e:
        await db.rollback()
        return JSONResponse({"ok": False, "message": f"Ошибка: {str(e)}"}, status_code=500)


@router.post("/telegram/bot-settings")
async def save_bot_settings(request: Request, db: AsyncSession = Depends(get_db)):
    _require_permission(request, "system")
    form = await request.form()
    allowed_keys = {
        "welcome_message",
        "btn_my_keys",
        "btn_buy",
        "btn_support",
        "btn_balance",
        "btn_promo",
        "support_url",
        "referral_bonus_days",
        "referral_bonus_type",
        "referral_bonus_value",
        "payment_success_message",
        "ban_message",
        "bot_disabled_message",
        "subscription_issued_message",
        "subscription_cancelled_message",
        "referral_welcome_message",
        "about_text",
        "unban_message",
        "required_channel_id",
        "required_channel_name",
        "photo_welcome",
        "photo_buy",
        "photo_my_keys",
        "photo_balance",
        "photo_about",
        "photo_support",
        "photo_profile",
        "panel_url",
        "required_channel_id",
        "required_channel_name",
        "btn_style_buy",
        "btn_style_my_keys",
        "btn_style_support",
        "btn_style_balance",
        "btn_style_promo",
        "btn_style_back",
        "btn_style_profile",
        "btn_style_connect",
        "btn_style_about",
        "btn_style_servers",
        "btn_style_top_referrers",
        "btn_style_status",
        "btn_style_language",
        "btn_emoji_buy",
        "btn_emoji_my_keys",
        "btn_emoji_support",
        "btn_emoji_balance",
        "btn_emoji_promo",
        "btn_emoji_profile",
        "btn_emoji_connect",
        "btn_emoji_about",
        "btn_emoji_servers",
        "btn_emoji_top_referrers",
        "btn_emoji_status",
        "btn_emoji_language",
        "bot_language",
        "cryptobot_token",
        "trial_enabled",
        "trial_days",
        "trial_label",
        "notify_expiry_enabled",
        "notify_expiry_days",
        "notify_expiry_message",
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
    _require_permission(request, "system")
    form = await request.form()
    lang = form.get("lang", "ru")
    if lang not in ("ru", "en", "fa"):
        return Response(status_code=400)
    # Whitelist of allowed i18n keys
    allowed_i18n_keys = {
        "welcome",
        "welcome_back",
        "btn_my_keys",
        "btn_buy",
        "btn_balance",
        "btn_promo",
        "btn_support",
        "btn_language",
        "choose_plan",
        "payment_success",
        "no_keys",
        "choose_language",
        "language_set",
        "main_menu",
        "enter_promo",
        "support_title",
        "support_no_tickets",
        "support_tickets",
        "new_ticket",
        "ticket_subject",
        "ticket_message",
        "ticket_created",
        "ticket_closed",
        "ticket_reply_sent",
        "ticket_not_found",
        "write_reply",
        "close_ticket",
        "payment_error",
        "payment_pending",
        "payment_failed",
        "payment_go",
        "payment_check",
        "pay_card",
        "pay_stars",
        "pay_crypto",
        "pay_balance",
        "no_plans",
        "key_error",
        "subscription_url",
        "balance_title",
        "referrals_count",
        "referral_bonus",
        "referral_link",
        "promo_balance",
        "promo_days",
        "promo_discount",
        "promo_invalid",
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
    _require_permission(request, "system")
    svc = BotSettingsService(db)
    current = await svc.get("bot_enabled") or "1"
    new_val = "0" if current == "1" else "1"
    await svc.set("bot_enabled", new_val)
    await db.commit()
    resp = Response(status_code=200)
    _toast(resp, "Бот включён" if new_val == "1" else "Бот отключён")
    return resp


@router.post("/telegram/send")
async def telegram_send_view(
    request: Request, chat_id: int = Form(...), text: str = Form(...)
):
    _require_permission(request, "system")
    ok = await TelegramNotifyService().send_message(chat_id, text)
    resp = Response(status_code=200)
    _toast(
        resp,
        "Сообщение отправлено" if ok else "Ошибка отправки",
        "success" if ok else "error",
    )
    return resp


# ── Backup ────────────────────────────────────────────────────────────────────


@router.get("/backup", response_class=HTMLResponse)
async def backup_page(request: Request, db: AsyncSession = Depends(get_db)):
    _require_permission(request, "system")
    ctx = await _base_ctx(request, db, "backup")

    from sqlalchemy import func, select
    from app.models.user import User
    from app.models.vpn_key import VpnKey
    from app.models.payment import Payment
    from app.models.support import SupportTicket

    ctx["db_stats"] = {
        "users": (await db.execute(select(func.count()).select_from(User))).scalar_one(),
        "vpn_keys": (await db.execute(select(func.count()).select_from(VpnKey))).scalar_one(),
        "payments": (await db.execute(select(func.count()).select_from(Payment))).scalar_one(),
        "tickets": (await db.execute(select(func.count()).select_from(SupportTicket))).scalar_one(),
    }

    return templates.TemplateResponse("backup.html", ctx)


@router.get("/backup/export")
async def backup_export(request: Request, format: str = "sql"):
    _require_permission(request, "system")
    db_cfg = config.database
    pg_uri = f"postgresql://{db_cfg.db_user}:{db_cfg.db_password.get_secret_value()}@{db_cfg.db_host}:{db_cfg.db_port}/{db_cfg.db_name}"
    cmd = [
        "pg_dump",
        "--no-password",
        "--clean",
        "--if-exists",
        pg_uri,
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, timeout=120)
        if result.returncode != 0:
            err = result.stderr.decode(errors="replace")[:300]
            return Response(content=f"pg_dump error: {err}", status_code=500)
        sql_bytes = result.stdout
    except FileNotFoundError:
        return Response(
            content="pg_dump not found. Install postgresql-client on the server.",
            status_code=500,
        )
    except subprocess.TimeoutExpired:
        return Response(content="pg_dump timed out", status_code=500)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    if format == "gz":
        buf = io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
            gz.write(sql_bytes)
        buf.seek(0)
        return StreamingResponse(
            buf,
            media_type="application/gzip",
            headers={
                "Content-Disposition": f'attachment; filename="backup_{ts}.sql.gz"'
            },
        )

    return StreamingResponse(
        io.BytesIO(sql_bytes),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="backup_{ts}.sql"'},
    )


_MAX_BACKUP = 100 * 1024 * 1024  # 100MB


@router.post("/backup/import", response_class=HTMLResponse)
async def backup_import(
    request: Request,
    file: UploadFile = File(...),
    confirm: Optional[str] = Form(None),
):
    _require_permission(request, "system")
    if confirm != "yes":
        resp = Response(status_code=400)
        _toast(resp, "Подтвердите восстановление", "error")
        return resp

    content = await file.read()
    if len(content) > _MAX_BACKUP:
        resp = Response(status_code=413)
        _toast(resp, "Файл слишком большой (макс. 100MB)", "error")
        return resp
    filename = file.filename or ""

    # Decompress if gzip
    if filename.endswith(".gz"):
        try:
            content = gzip.decompress(content)
        except Exception:
            resp = Response(status_code=400)
            _toast(resp, "Не удалось распаковать .gz файл", "error")
            return resp

    # Strip PostgreSQL 17+ transaction_timeout (incompatible with PG 15)
    content = re.sub(rb"SET transaction_timeout = [^;]+;\r?\n?", b"", content)

    # Pre-clean: drop all tables in public schema to handle old backups without --clean
    content = (
        b"DO $$ DECLARE r RECORD; BEGIN "
        b"FOR r IN (SELECT tablename FROM pg_tables WHERE schemaname = 'public') LOOP "
        b"EXECUTE 'DROP TABLE IF EXISTS public.' || quote_ident(r.tablename) || ' CASCADE'; "
        b"END LOOP; END $$;\n"
    ) + content

    db_cfg = config.database
    pg_uri = f"postgresql://{db_cfg.db_user}:{db_cfg.db_password.get_secret_value()}@{db_cfg.db_host}:{db_cfg.db_port}/{db_cfg.db_name}"
    cmd = [
        "psql",
        "--no-password",
        "-v",
        "ON_ERROR_STOP=1",
        "--single-transaction",
        pg_uri,
    ]

    try:
        result = subprocess.run(
            cmd, input=content, capture_output=True, timeout=300
        )
        if result.returncode != 0:
            err = result.stderr.decode(errors="replace")[:800]
            out = result.stdout.decode(errors="replace")[:800]
            detail = err or out or "unknown error"
            log.error(f"Backup restore failed: {detail}")
            resp = Response(status_code=500)
            _toast(resp, f"Ошибка восстановления: {detail[:120]}", "error")
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
    _require_permission(request, "system")
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
    _require_permission(request, "system")
    from app.services.pasarguard.pasarguard import PasarguardService

    try:
        data = await PasarguardService().get_users(limit=50)
        users = data.get("users", []) if isinstance(data, dict) else data
    except Exception as e:
        return HTMLResponse(f'<div style="color:#ef4444">Ошибка: {html.escape(str(e))}</div>')

    if not users:
        return HTMLResponse('<div style="color:#8892a4">Нет пользователей</div>')

    rows = ""
    for u in users:
        status = u.get("status", "")
        color = {"active": "#22c55e", "expired": "#ef4444", "disabled": "#eab308"}.get(
            status, "#8892a4"
        )
        used = round((u.get("used_traffic", 0) or 0) / 1073741824, 2)
        limit = u.get("data_limit", 0) or 0
        limit_str = f"{round(limit / 1073741824, 1)} GB" if limit else "∞"
        username = html.escape(str(u.get("username", "")))
        expire = html.escape(str(u.get("expire", "—") or "—"))
        rows += f"""<tr class="user-row">
          <td><code style="color:var(--accent)">{username}</code></td>
          <td><span style="color:{color};font-size:.75rem">{html.escape(str(status))}</span></td>
          <td style="font-size:.78rem;color:#8892a4">{used} / {limit_str}</td>
          <td style="font-size:.75rem;color:#8892a4">{expire}</td>
        </tr>"""

    return HTMLResponse(f"""
    <div class="table-responsive">
    <table class="table mb-0">
      <thead><tr><th>Username</th><th>Статус</th><th>Трафик</th><th>Истекает</th></tr></thead>
      <tbody>{rows}</tbody>
    </table></div>""")


@router.get("/pasarguard/groups", response_class=HTMLResponse)
async def pg_groups(request: Request):
    _require_permission(request, "system")
    from app.services.pasarguard.pasarguard import PasarguardService

    try:
        groups = await PasarguardService().get_groups()
    except Exception as e:
        return HTMLResponse(f'<div class="p-3" style="color:#ef4444">Ошибка: {html.escape(str(e))}</div>')

    if not groups:
        return HTMLResponse('<div class="p-3" style="color:#8892a4">Групп нет</div>')

    rows = ""
    for g in groups:
        disabled = "🔴" if g.get("is_disabled") else "🟢"
        inbounds = ", ".join(g.get("inbound_tags", []))
        group_name = html.escape(str(g.get("name", "")))
        rows += f"""<tr>
          <td><code style="color:var(--accent)">{g["id"]}</code></td>
          <td class="text-white">{group_name}</td>
          <td style="font-size:.75rem;color:#8892a4">{html.escape(inbounds)}</td>
          <td>{disabled}</td>
          <td style="color:#8892a4">{g.get("total_users", 0)}</td>
        </tr>"""

    return HTMLResponse(f"""
    <div class="table-responsive">
    <table class="table mb-0">
      <thead><tr><th>ID</th><th>Название</th><th>Inbounds</th><th>Статус</th><th>Юзеров</th></tr></thead>
      <tbody>{rows}</tbody>
    </table></div>""")


@router.get("/pasarguard/nodes", response_class=HTMLResponse)
async def pg_nodes(request: Request):
    _require_permission(request, "system")
    from app.services.pasarguard.pasarguard import PasarguardService

    try:
        data = await PasarguardService().get_nodes()
        nodes = data.get("nodes", []) if isinstance(data, dict) else data
    except Exception as e:
        return HTMLResponse(f'<div class="p-3" style="color:#ef4444">Ошибка: {html.escape(str(e))}</div>')

    if not nodes:
        return HTMLResponse('<div class="p-3" style="color:#8892a4">Нод нет</div>')

    rows = ""
    for n in nodes:
        status = n.get("status", "")
        color = {
            "connected": "#22c55e",
            "connecting": "#eab308",
            "error": "#ef4444",
        }.get(status, "#8892a4")
        node_name = html.escape(str(n.get("name", "")))
        node_addr = html.escape(str(n.get("address", "")))
        rows += f"""<tr>
          <td><code style="color:var(--accent)">{html.escape(str(n.get("id", "")))}</code></td>
          <td class="text-white">{node_name}</td>
          <td style="color:#8892a4;font-size:.8rem">{node_addr}</td>
          <td><span style="color:{color};font-size:.75rem">{html.escape(str(status))}</span></td>
        </tr>"""

    return HTMLResponse(f"""
    <div class="table-responsive">
    <table class="table mb-0">
      <thead><tr><th>ID</th><th>Название</th><th>Адрес</th><th>Статус</th></tr></thead>
      <tbody>{rows}</tbody>
    </table></div>""")




# ── Nodes ─────────────────────────────────────────────────────────────────────


@router.get("/nodes", response_class=HTMLResponse)
async def nodes_page(request: Request, db: AsyncSession = Depends(get_db)):
    admin_info = _require_permission(request, "vpn.read")
    ctx = await _base_ctx(request, db, "nodes", admin_info)
    from app.services.pasarguard.pasarguard import PasarguardService
    try:
        data = await PasarguardService().get_nodes()
        ctx["nodes"] = data.get("nodes", []) if isinstance(data, dict) else data
    except Exception:
        ctx["nodes"] = []
    return templates.TemplateResponse("nodes.html", ctx)


@router.get("/nodes/data", response_class=HTMLResponse)
async def nodes_data(request: Request):
    _require_permission(request, "vpn.read")
    from app.services.pasarguard.pasarguard import PasarguardService
    try:
        data = await PasarguardService().get_nodes()
        nodes = data.get("nodes", []) if isinstance(data, dict) else data
    except Exception as e:
        return HTMLResponse(f'''<div class="p-3" style="color:var(--danger)">Ошибка: {html.escape(str(e))}</div>''')

    if not nodes:
        return HTMLResponse('''<div class="p-3 text-muted">Нод нет</div>''')

    cards = ""
    for n in nodes:
        status = n.get("status", "")
        color = {"connected": "var(--success)", "connecting": "var(--warning)", "error": "var(--danger)"}.get(status, "var(--muted)")
        pulse = "animation: pulse-glow 2s infinite" if status == "connecting" else ""
        node_name = html.escape(str(n.get("name", "")))
        node_addr = html.escape(str(n.get("address", "")))
        node_id = html.escape(str(n.get("id", "")))
        cards += f'''
        <div class="col-md-6 col-xl-4">
          <div class="card glass p-3 h-100">
            <div class="d-flex align-items-center justify-content-between mb-2">
              <div class="d-flex align-items-center gap-2">
                <span style="width:10px;height:10px;border-radius:50%;background:{color};box-shadow:0 0 8px {color};{pulse}"></span>
                <span class="fw-semibold" style="color:var(--text)">{node_name}</span>
              </div>
              <span style="font-size:.7rem;color:var(--muted)">#{node_id}</span>
            </div>
            <div style="font-size:.78rem;color:var(--muted);margin-bottom:.5rem">{node_addr}</div>
            <div class="d-flex gap-3 mb-2" style="font-size:.75rem;color:var(--muted)">
              <span><i class="bi bi-hdd-network me-1"></i>{html.escape(str(n.get("port", "—")))}</span>
              <span><i class="bi bi-people me-1"></i>{html.escape(str(n.get("total_users", 0)))}</span>
            </div>
            <div class="d-flex gap-2 mt-auto">
              <button class="btn btn-sm btn-outline" hx-post="/panel/nodes/{node_id}/reconnect" hx-target="#nodes-grid" hx-swap="innerHTML">
                <i class="bi bi-arrow-clockwise me-1"></i>Переподключить
              </button>
            </div>
          </div>
        </div>'''

    return HTMLResponse(f'''<div class="row g-3" id="nodes-grid" hx-get="/panel/nodes/data" hx-trigger="every 30s" hx-swap="outerHTML">{cards}</div>''')


@router.post("/nodes/{node_id}/reconnect", response_class=HTMLResponse)
async def reconnect_node(node_id: int, request: Request):
    _require_permission(request, "system")
    from app.services.pasarguard.pasarguard import PasarguardService
    try:
        await PasarguardService().reconnect_node(node_id)
    except Exception as e:
        return HTMLResponse(f'''<div class="p-3" style="color:var(--danger)">Ошибка: {html.escape(str(e))}</div>''')
    return await nodes_data(request)


@router.post("/nodes/add", response_class=HTMLResponse)
async def add_node(
    request: Request,
    name: str = Form(...),
    address: str = Form(...),
    port: int = Form(62050),
    api_port: int = Form(62051),
    db: AsyncSession = Depends(get_db),
):
    _require_permission(request, "system")
    from app.services.pasarguard.pasarguard import PasarguardService
    try:
        await PasarguardService().add_node(name=name, address=address, port=port, api_port=api_port)
    except Exception as e:
        resp = Response(status_code=400)
        _toast(resp, f"Ошибка добавления ноды: {html.escape(str(e))[:100]}", "error")
        return resp
    return await nodes_data(request)


@router.delete("/nodes/{node_id}", response_class=HTMLResponse)
async def delete_node(node_id: int, request: Request):
    _require_permission(request, "system")
    from app.services.pasarguard.pasarguard import PasarguardService
    try:
        await PasarguardService().remove_node(node_id)
    except Exception as e:
        resp = Response(status_code=400)
        _toast(resp, f"Ошибка удаления ноды: {html.escape(str(e))[:100]}", "error")
        return resp
    return await nodes_data(request)


# ── Export ────────────────────────────────────────────────────────────────────


@router.get("/export/users")
async def export_users(
    request: Request,
    format: str = "csv",
    db: AsyncSession = Depends(get_db),
):
    _require_permission(request, "export")
    data = await ExportService(db).export_users(fmt=format)
    ext = "xlsx" if format == "xlsx" else "csv"
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if ext == "xlsx" else "text/csv"
    return StreamingResponse(
        io.BytesIO(data),
        media_type=mime,
        headers={"Content-Disposition": f'attachment; filename="users_{ts}.{ext}"'},
    )


@router.get("/export/payments")
async def export_payments(
    request: Request,
    format: str = "csv",
    status: Optional[str] = None,
    payment_type: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    _require_permission(request, "export")
    data = await ExportService(db).export_payments(
        fmt=format, status=status, payment_type=payment_type,
        date_from=date_from, date_to=date_to,
    )
    ext = "xlsx" if format == "xlsx" else "csv"
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if ext == "xlsx" else "text/csv"
    return StreamingResponse(
        io.BytesIO(data),
        media_type=mime,
        headers={"Content-Disposition": f'attachment; filename="payments_{ts}.{ext}"'},
    )


@router.get("/export/subscriptions")
async def export_subscriptions(
    request: Request,
    format: str = "csv",
    db: AsyncSession = Depends(get_db),
):
    _require_permission(request, "export")
    data = await ExportService(db).export_subscriptions(fmt=format)
    ext = "xlsx" if format == "xlsx" else "csv"
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if ext == "xlsx" else "text/csv"
    return StreamingResponse(
        io.BytesIO(data),
        media_type=mime,
        headers={"Content-Disposition": f'attachment; filename="subscriptions_{ts}.{ext}"'},
    )


# ── WebSocket Token ───────────────────────────────────────────────────────────


@router.get("/ws-token")
async def ws_token(request: Request):
    """Return a short-lived token for WebSocket authentication."""
    admin_info = _require_auth(request)
    from app.utils.security import create_access_token
    token = create_access_token(
        subject=admin_info["sub"],
        role=admin_info["role"],
        expires_delta=timedelta(minutes=1),
    )
    return {"token": token}

# ── Admins ────────────────────────────────────────────────────────────────────

@router.get("/admins", response_class=HTMLResponse)
async def admins_page(request: Request, db: AsyncSession = Depends(get_db)):
    _require_permission(request, "system")
    ctx = await _base_ctx(request, db, "admins")
    ctx["admins"] = await AdminService(db).get_all()
    return templates.TemplateResponse("admins.html", ctx)


@router.post("/admins", response_class=HTMLResponse)
async def create_admin(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    role: str = Form("operator"),
    db: AsyncSession = Depends(get_db),
):
    _require_permission(request, "system")
    if role not in ("superadmin", "manager", "operator"):
        resp = Response(status_code=400)
        _toast(resp, "Недопустимая роль", "error")
        return resp
    existing = await AdminService(db).get_by_username(username)
    if existing:
        resp = Response(status_code=400)
        _toast(resp, "Администратор с таким именем уже существует", "error")
        return resp
    await AdminService(db).create(username=username, password=password, role=role)
    await db.commit()
    admins = await AdminService(db).get_all()
    resp = templates.TemplateResponse(
        "partials/admins_table.html",
        {"request": request, "admins": admins},
    )
    _toast(resp, f"Администратор {username} создан")
    return resp


@router.post("/admins/{admin_id}/edit", response_class=HTMLResponse)
async def edit_admin(
    admin_id: int,
    request: Request,
    username: Optional[str] = Form(None),
    password: Optional[str] = Form(None),
    role: Optional[str] = Form(None),
    is_active: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
):
    _require_permission(request, "system")
    admin_info = _get_admin_info(request)
    # Prevent self-demotion from superadmin
    target = await AdminService(db).get_by_id(admin_id)
    if not target:
        resp = Response(status_code=404)
        _toast(resp, "Администратор не найден", "error")
        return resp
    if target.username == admin_info["sub"] and role and role != "superadmin":
        resp = Response(status_code=400)
        _toast(resp, "Нельзя понизить самого себя", "error")
        return resp
    updates = {}
    if username is not None:
        updates["username"] = username
    if password is not None and password.strip():
        updates["password"] = password
    if role is not None:
        updates["role"] = role
    if is_active is not None:
        updates["is_active"] = is_active == "1"
    await AdminService(db).update(admin_id, **updates)
    await db.commit()
    admins = await AdminService(db).get_all()
    resp = templates.TemplateResponse(
        "partials/admins_table.html",
        {"request": request, "admins": admins},
    )
    _toast(resp, "Администратор обновлён")
    return resp


@router.delete("/admins/{admin_id}", response_class=HTMLResponse)
async def delete_admin(admin_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    _require_permission(request, "system")
    admin_info = _get_admin_info(request)
    target = await AdminService(db).get_by_id(admin_id)
    if not target:
        resp = Response(status_code=404)
        _toast(resp, "Администратор не найден", "error")
        return resp
    if target.username == admin_info["sub"]:
        resp = Response(status_code=400)
        _toast(resp, "Нельзя удалить самого себя", "error")
        return resp
    await AdminService(db).delete(admin_id)
    await db.commit()
    admins = await AdminService(db).get_all()
    resp = templates.TemplateResponse(
        "partials/admins_table.html",
        {"request": request, "admins": admins},
    )
    _toast(resp, "Администратор удалён")
    return resp


# ── Keyboard Editor ─────────────────────────────────────────────────────────--

# All available buttons definition
_ALL_BUTTONS = [
    {"id": "my_keys", "label": "🔑 Мои подписки", "callback": "my_keys"},
    {"id": "buy", "label": "💳 Купить", "callback": "buy"},
    {"id": "profile", "label": "👤 Профиль", "callback": "profile"},
    {"id": "balance", "label": "💰 Баланс", "callback": "balance"},
    {"id": "promo", "label": "🎁 Промокод", "callback": "enter_promo"},
    {"id": "support", "label": "💬 Поддержка", "callback": "support"},
    {"id": "connect", "label": "📲 Как подключить", "callback": "connect:menu"},
    {"id": "about", "label": "ℹ️ О проекте", "callback": "about"},
    {"id": "servers", "label": "🌐 Серверы", "callback": "servers"},
    {"id": "top_referrers", "label": "🏆 Топ рефереров", "callback": "top_referrers"},
    {"id": "status", "label": "📊 Статус", "callback": "status_cmd"},
    {"id": "language", "label": "🌐 Язык", "callback": "language"},
    {"id": "trial", "label": "🎁 Пробный период", "callback": "trial"},
    {"id": "miniapp", "label": "📱 Открыть", "callback": "miniapp"},
]

_DEFAULT_LAYOUT = [
    [{"id": "my_keys", "label": "🔑 Мои подписки", "callback": "my_keys"}],
    [{"id": "buy", "label": "💳 Купить", "callback": "buy"}],
    [
        {"id": "balance", "label": "💰 Баланс", "callback": "balance"},
        {"id": "promo", "label": "🎁 Промокод", "callback": "enter_promo"},
    ],
    [
        {"id": "connect", "label": "📲 Как подключить", "callback": "connect:menu"},
        {"id": "about", "label": "ℹ️ О проекте", "callback": "about"},
    ],
    [
        {"id": "profile", "label": "👤 Профиль", "callback": "profile"},
        {"id": "servers", "label": "🌐 Серверы", "callback": "servers"},
    ],
    [{"id": "top_referrers", "label": "🏆 Топ рефереров", "callback": "top_referrers"}],
    [{"id": "support", "label": "💬 Поддержка", "callback": "support"}],
]


@router.get("/keyboard", response_class=HTMLResponse)
async def keyboard_editor(request: Request, db: AsyncSession = Depends(get_db)):
    _require_permission(request, "system")
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
    ctx["welcome_text"] = (
        await BotSettingsService(db).get("welcome_message")
        or "👋 Привет! Выбери действие:"
    )

    # Used IDs
    used_ids = [b["id"] for row in layout for b in row]
    ctx["used_ids"] = used_ids

    return templates.TemplateResponse("keyboard_editor.html", ctx)


@router.post("/keyboard/save")
async def keyboard_save(request: Request, db: AsyncSession = Depends(get_db)):
    _require_permission(request, "system")
    import json as _json

    body = await request.json()
    layout = body.get("layout", _DEFAULT_LAYOUT)
    await BotSettingsService(db).set("keyboard_layout", _json.dumps(layout))
    await db.commit()
    return {"ok": True}


@router.post("/keyboard/styles")
async def keyboard_styles(request: Request, db: AsyncSession = Depends(get_db)):
    _require_permission(request, "system")
    body = await request.json()
    styles = body.get("styles", {})
    svc = BotSettingsService(db)
    for btn_id, style in styles.items():
        await svc.set(f"btn_style_{btn_id}", style)
    await db.commit()
    return {"ok": True}


@router.get("/audit", response_class=HTMLResponse)
async def audit_page(request: Request, db: AsyncSession = Depends(get_db)):
    _require_permission(request, "system")
    from app.services.audit import AuditService

    ctx = await _base_ctx(request, db, "audit")
    ctx["entries"] = await AuditService(db).get_recent(limit=200)
    return templates.TemplateResponse("audit.html", ctx)


@router.get("/monitoring", response_class=HTMLResponse)
async def monitoring_page(request: Request, db: AsyncSession = Depends(get_db)):
    _require_permission(request, "system")
    from app.services.health import health_service, ServiceStatus

    entries = await health_service.check_all()
    services = {}
    for name, entry in entries.items():
        services[name] = {
            "status": entry.status,
            "latency_ms": entry.latency_ms,
            "message": entry.message,
            "checked_at": entry.checked_at,
        }

    ctx = await _base_ctx(request, db, "monitoring")
    ctx["services"] = services
    from app.services.slow_query import get_slow_queries
    ctx["slow_queries"] = get_slow_queries()[-50:]
    ctx["uptime"] = _get_uptime()
    return templates.TemplateResponse("monitoring.html", ctx)


@router.get("/health/json")
async def health_json(request: Request, db: AsyncSession = Depends(get_db)):
    _require_permission(request, "system")
    from fastapi.responses import JSONResponse
    from app.services.health import health_service

    entries = await health_service.check_all()
    result = {}
    for name, entry in entries.items():
        result[name] = {
            "status": entry.status,
            "latency_ms": entry.latency_ms,
            "message": entry.message,
            "checked_at": entry.checked_at.isoformat() if entry.checked_at else None,
        }
    return JSONResponse(result)


_startup_time = datetime.now(timezone.utc)


def _get_uptime() -> str:
    from datetime import timedelta
    delta = datetime.now(timezone.utc) - _startup_time
    days = delta.days
    hours, remainder = divmod(delta.seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    if days > 0:
        return f"{days}d {hours}h"
    elif hours > 0:
        return f"{hours}h {minutes}m"
    else:
        return f"{minutes}m"
