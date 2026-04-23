from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import config
from app.utils.log import log

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7


def _secret_key() -> str:
    """Return a dedicated JWT signing secret.

    Falls back to the superadmin password only for backward compatibility,
    but logs a warning because rotating the admin password would invalidate
    all active sessions when the password is used as the secret.
    """
    import os

    secret = os.environ.get("JWT_SECRET_KEY", "").strip()
    if secret:
        return secret
    # Fallback: derive a stable secret from the superadmin password.
    # NOTE: Changing the superadmin password will log out all admins.
    log.warning(
        "JWT_SECRET_KEY is not set; using web_superadmin_password as fallback. "
        "Set JWT_SECRET_KEY in your environment to allow password rotation without invalidating tokens."
    )
    return config.web.web_superadmin_password.get_secret_value()


def hash_password(password: str) -> str:
    return _pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return _pwd_context.verify(plain, hashed)


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
