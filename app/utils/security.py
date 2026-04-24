from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import bcrypt
from jose import JWTError, jwt

from app.core.config import config
from app.utils.log import log

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7


def _secret_key() -> str:
    """Return a dedicated JWT signing secret."""
    import os
    secret = os.environ.get("JWT_SECRET_KEY", "").strip()
    if secret:
        return secret
    log.warning("JWT_SECRET_KEY not set; using web_superadmin_password fallback")
    return config.web.web_superadmin_password.get_secret_value()


def hash_password(password: str) -> str:
    """Hash password with bcrypt (auto-generates salt, handles encoding)."""
    # bcrypt has 72-byte limit; passlib does this internally, we do it explicitly
    pw_bytes = password.encode("utf-8")[:72]
    return bcrypt.hashpw(pw_bytes, bcrypt.gensalt(rounds=12)).decode("ascii")


def verify_password(plain: str, hashed: str) -> bool:
    """Verify plain password against bcrypt hash."""
    try:
        pw_bytes = plain.encode("utf-8")[:72]
        hash_bytes = hashed.encode("ascii")
        return bcrypt.checkpw(pw_bytes, hash_bytes)
    except Exception:
        return False


def create_access_token(
    subject: Any,
    role: str = "superadmin",
    expires_delta: Optional[timedelta] = None,
) -> str:
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    payload = {"sub": str(subject), "role": role, "exp": expire}
    return jwt.encode(payload, _secret_key(), algorithm=ALGORITHM)


def decode_access_token(token: str) -> Optional[str]:
    """Returns subject (str) or None if token is invalid/expired."""
    try:
        payload = jwt.decode(token, _secret_key(), algorithms=[ALGORITHM])
        return payload.get("sub")
    except JWTError:
        return None


def decode_access_token_full(token: str) -> Optional[dict]:
    """Returns {"sub": str, "role": str} or None if token is invalid/expired."""
    try:
        payload = jwt.decode(token, _secret_key(), algorithms=[ALGORITHM])
        sub = payload.get("sub")
        if sub is None:
            return None
        return {"sub": sub, "role": payload.get("role", "superadmin")}
    except JWTError:
        return None
