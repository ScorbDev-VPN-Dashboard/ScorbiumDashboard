import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional
from httpx import AsyncClient, HTTPStatusError, RequestError

from app.core.config import config
from app.utils.log import log
from app.core.exceptions import PasarguardRequestError, PasarguardAuthError
from app.services.vpn_panel_interface import VpnPanelInterface


class MarzbanClient:
    """Low-level async HTTP client for Marzban panel."""

    _token: Optional[str] = None
    _token_expires: Optional[datetime] = None
    _lock = asyncio.Lock()

    def __init__(self) -> None:
        cfg = config.pasarguard
        if cfg is None:
            raise RuntimeError(
                "Marzban/Pasarguard is not configured. Check PASARGUARD_ADMIN_PANEL in .env"
            )
        self._base = str(cfg.pasarguard_admin_panel).rstrip("/")
        self._login = cfg.pasarguard_admin_login
        self._password = (
            cfg.pasarguard_admin_password.get_secret_value()
            if cfg.pasarguard_admin_password
            else None
        )
        self._api_key = (
            cfg.pasarguard_api_key.get_secret_value()
            if cfg.pasarguard_api_key
            else None
        )

    async def _get_token(self) -> str:
        """OAuth2 password flow — получаем Bearer token."""
        async with self._lock:
            now = datetime.now(timezone.utc)
            if self._token and self._token_expires and now < self._token_expires:
                return self._token

            if not self._login or not self._password:
                raise PasarguardAuthError("Marzban login/password not configured")

            async with AsyncClient(timeout=15, verify=False) as client:
                resp = await client.post(
                    f"{self._base}/api/admin/token",
                    data={"username": self._login, "password": self._password},
                )
                if resp.status_code != 200:
                    raise PasarguardAuthError(
                        f"Marzban auth failed: {resp.status_code} {resp.text}"
                    )
                data = resp.json()
                self._token = data["access_token"]
                self._token_expires = now + timedelta(hours=23)
                log.info("✅ Marzban token refreshed")
                return self._token

    async def _headers(self) -> dict:
        # Ignore static API key — always use auto-refreshing login/password flow
        token = await self._get_token()
        return {"Authorization": f"Bearer {token}"}

    async def _headers_force_refresh(self) -> dict:
        """Force token refresh — used after 401."""
        async with self._lock:
            MarzbanClient._token = None
            MarzbanClient._token_expires = None
        return await self._headers()

    async def get(self, path: str, params: dict = None) -> dict:
        url = f"{self._base}{path}"
        try:
            async with AsyncClient(timeout=15, verify=False) as client:
                resp = await client.get(
                    url, headers=await self._headers(), params=params
                )
                if resp.status_code == 401:
                    # Token expired — refresh and retry once
                    resp = await client.get(
                        url, headers=await self._headers_force_refresh(), params=params
                    )
                resp.raise_for_status()
                return resp.json()
        except HTTPStatusError as e:
            log.error(f"Marzban GET {path} → {e.response.status_code}")
            raise PasarguardRequestError(f"HTTP {e.response.status_code}")
        except RequestError as e:
            raise PasarguardRequestError(f"Connection error: {e}")

    async def post(self, path: str, payload: dict = None) -> dict:
        url = f"{self._base}{path}"
        try:
            async with AsyncClient(timeout=15, verify=False) as client:
                resp = await client.post(
                    url, headers=await self._headers(), json=payload or {}
                )
                if resp.status_code == 401:
                    resp = await client.post(
                        url,
                        headers=await self._headers_force_refresh(),
                        json=payload or {},
                    )
                resp.raise_for_status()
                return resp.json() if resp.content else {}
        except HTTPStatusError as e:
            log.error(
                f"Marzban POST {path} → {e.response.status_code}: {e.response.text}"
            )
            raise PasarguardRequestError(
                f"HTTP {e.response.status_code}: {e.response.text}"
            )
        except RequestError as e:
            raise PasarguardRequestError(f"Connection error: {e}")

    async def put(self, path: str, payload: dict = None) -> dict:
        url = f"{self._base}{path}"
        try:
            async with AsyncClient(timeout=15, verify=False) as client:
                resp = await client.put(
                    url, headers=await self._headers(), json=payload or {}
                )
                if resp.status_code == 401:
                    resp = await client.put(
                        url,
                        headers=await self._headers_force_refresh(),
                        json=payload or {},
                    )
                resp.raise_for_status()
                return resp.json() if resp.content else {}
        except HTTPStatusError as e:
            raise PasarguardRequestError(
                f"HTTP {e.response.status_code}: {e.response.text}"
            )
        except RequestError as e:
            raise PasarguardRequestError(f"Connection error: {e}")

    async def delete(self, path: str) -> None:
        url = f"{self._base}{path}"
        try:
            async with AsyncClient(timeout=15, verify=False) as client:
                resp = await client.delete(url, headers=await self._headers())
                if resp.status_code == 401:
                    resp = await client.delete(
                        url, headers=await self._headers_force_refresh()
                    )
                resp.raise_for_status()
        except HTTPStatusError as e:
            raise PasarguardRequestError(f"HTTP {e.response.status_code}")
        except RequestError as e:
            raise PasarguardRequestError(f"Connection error: {e}")


class PasarguardService(VpnPanelInterface):
    """
    High-level Marzban/Pasarguard API service.
    Implements VpnPanelInterface.
    """

    def __init__(self) -> None:
        self._client = MarzbanClient()

    # ── System ──────────────────────────────────────────────────────────────

    async def get_system_stats(self) -> dict:
        """Статистика системы: онлайн, трафик, пользователи."""
        return await self._client.get("/api/system")

    async def validate_connection(self) -> bool:
        try:
            await self._client.get("/api/system")
            return True
        except Exception as e:
            log.warning(f"Marzban connection check failed: {e}")
            return False

    # ── Users ───────────────────────────────────────────────────────────────

    async def get_users(
        self, offset: int = 0, limit: int = 100, status: Optional[str] = None
    ) -> dict:
        """Список VPN пользователей."""
        params = {"offset": offset, "limit": limit}
        if status:
            params["status"] = status
        return await self._client.get("/api/users", params=params)

    async def get_user(self, username: str) -> Optional[dict]:
        """Получить VPN пользователя по username."""
        try:
            return await self._client.get(f"/api/user/{username}")
        except PasarguardRequestError:
            return None

    async def create_user(
        self,
        username: str,
        expire_days: int = 30,
        data_limit_gb: int = 0,
        proxies: Optional[dict] = None,
        group_ids: Optional[list] = None,
    ) -> dict:
        import uuid

        expire_ts = None
        if expire_days > 0:
            expire_ts = (
                datetime.now(timezone.utc) + timedelta(days=expire_days)
            ).isoformat()

        uid = str(uuid.uuid4())
        proxy_settings = proxies or {
            "vmess": {"id": uid},
            "vless": {"id": uid, "flow": ""},
            "trojan": {"password": uid[:16]},
            "shadowsocks": {
                "password": uid.replace("-", "")[:22],
                "method": "chacha20-ietf-poly1305",
            },
        }

        payload = {
            "username": username,
            "proxy_settings": proxy_settings,
            "expire": expire_ts,
            "data_limit": data_limit_gb * 1024**3 if data_limit_gb > 0 else 0,
            "data_limit_reset_strategy": "no_reset",
            "status": "active",
        }
        if group_ids:
            payload["group_ids"] = group_ids

        return await self._client.post("/api/user", payload)

    async def modify_user(self, username: str, **kwargs) -> dict:
        """Изменить параметры VPN пользователя."""
        return await self._client.put(f"/api/user/{username}", kwargs)

    async def delete_user(self, username: str) -> None:
        """Удалить VPN пользователя."""
        await self._client.delete(f"/api/user/{username}")

    async def reset_user_traffic(self, username: str) -> dict:
        """Сбросить трафик пользователя."""
        return await self._client.post(f"/api/user/{username}/reset")

    async def revoke_user_subscription(self, username: str) -> dict:
        """Перевыпустить ссылку подписки пользователя."""
        return await self._client.post(f"/api/user/{username}/revoke_sub")

    async def extend_user(self, username: str, extra_days: int) -> dict:
        """Продлить подписку пользователя на extra_days дней."""
        user = await self.get_user(username)
        if not user:
            raise PasarguardRequestError(f"User {username} not found")

        raw_expire = user.get("expire")

        now = datetime.now(timezone.utc)

        current_expire = None
        if raw_expire is not None:
            try:
                s = str(raw_expire).strip()
                if not s or s.lower() == "none":
                    current_expire = None
                elif s.isdigit():
                    ts = int(s)
                    current_expire = (
                        datetime.fromtimestamp(ts, tz=timezone.utc) if ts > 0 else now
                    )
                else:
                    try:
                        current_expire = datetime.fromisoformat(
                            s.replace("Z", "+00:00")
                        )
                    except ValueError:
                        try:
                            ts = float(s)
                            current_expire = (
                                datetime.fromtimestamp(ts, tz=timezone.utc)
                                if ts > 0
                                else now
                            )
                        except ValueError:
                            current_expire = now
            except Exception as e:
                log.warning(f"[extend_user] parse expire error: {e}")
                current_expire = now

        if current_expire is None:
            base = now
        else:
            base = current_expire

        if base < now:
            base = now

        new_expire = (base + timedelta(days=extra_days)).isoformat()
        log.info(f"[extend_user] base={base} new_expire={new_expire}")
        return await self.modify_user(username, expire=new_expire)

    async def disable_user(self, username: str) -> dict:
        return await self.modify_user(username, status="disabled")

    async def enable_user(self, username: str) -> dict:
        return await self.modify_user(username, status="active")

    # ── Nodes ──────────────────────────────────────────────────────────────

    async def get_nodes(self) -> dict:
        return await self._client.get("/api/nodes")

    async def get_node_stats(self) -> dict:
        return await self._client.get("/api/nodes/realtime_stats")

    async def get_groups(self) -> list[dict]:
        """Список групп (inbound groups) из Marzban."""
        try:
            data = await self._client.get("/api/groups")
            return data.get("groups", [])
        except Exception as e:
            log.warning(f"Marzban get_groups failed: {e}")
            return []

    # ── Subscription link ──────────────────────────────────────────────────

    def get_subscription_url(self, sub_token: str) -> str:
        """Ссылка на подписку для клиента."""
        base = str(config.pasarguard.pasarguard_admin_panel).rstrip("/")
        return f"{base}/sub/{sub_token}/"


def get_vpn_panel() -> VpnPanelInterface:
    """Factory — returns Marzban/Pasarguard panel backend."""
    return PasarguardService()
