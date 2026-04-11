"""
Remnawave VPN panel API client.
Based on official Remnawave OpenAPI v2.7.4
"""
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

from httpx import AsyncClient, HTTPStatusError, RequestError

from app.services.vpn_panel_interface import VpnPanelInterface
from app.utils.log import log


class RemnawaveClient:
    """Low-level async HTTP client for Remnawave panel."""

    _token: Optional[str] = None
    _token_expires: Optional[datetime] = None
    _lock = asyncio.Lock()

    def __init__(self, base_url: str, login: str = None, password: str = None, api_key: str = None) -> None:
        self._base = base_url.rstrip("/")
        self._login = login
        self._password = password
        self._api_key = api_key  # если задан — используем напрямую, без логина

    async def _get_token(self) -> str:
        # API Key — постоянный, не нужно обновлять
        if self._api_key:
            return self._api_key

        async with self._lock:
            now = datetime.now(timezone.utc)
            if self._token and self._token_expires and now < self._token_expires:
                return self._token

            if not self._login or not self._password:
                raise RuntimeError("Remnawave: нужен REMNAWAVE_API_KEY или REMNAWAVE_LOGIN + REMNAWAVE_PASSWORD")

            async with AsyncClient(timeout=15, verify=False) as client:
                resp = await client.post(
                    f"{self._base}/api/auth/login",
                    json={"username": self._login, "password": self._password},
                )
                if resp.status_code not in (200, 201):
                    raise RuntimeError(
                        f"Remnawave auth failed: {resp.status_code} {resp.text}"
                    )
                data = resp.json()
                token_data = data.get("response", data)
                self._token = token_data.get("accessToken") or token_data.get("access_token")
                if not self._token:
                    raise RuntimeError(f"No accessToken in Remnawave response: {data}")
                self._token_expires = now + timedelta(hours=23)
                log.info("✅ Remnawave JWT token refreshed")
                return self._token

    async def _headers(self) -> dict:
        token = await self._get_token()
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    async def _refresh_and_headers(self) -> dict:
        if self._api_key:
            return await self._headers()  # API Key не протухает
        async with self._lock:
            RemnawaveClient._token = None
            RemnawaveClient._token_expires = None
        return await self._headers()

    async def get(self, path: str, params: dict = None) -> dict:
        url = f"{self._base}{path}"
        async with AsyncClient(timeout=15, verify=False) as client:
            resp = await client.get(url, headers=await self._headers(), params=params)
            if resp.status_code == 401:
                resp = await client.get(url, headers=await self._refresh_and_headers(), params=params)
            resp.raise_for_status()
            return resp.json()

    async def post(self, path: str, payload: dict = None) -> dict:
        url = f"{self._base}{path}"
        async with AsyncClient(timeout=15, verify=False) as client:
            resp = await client.post(url, headers=await self._headers(), json=payload or {})
            if resp.status_code == 401:
                resp = await client.post(url, headers=await self._refresh_and_headers(), json=payload or {})
            resp.raise_for_status()
            return resp.json() if resp.content else {}

    async def patch(self, path: str, payload: dict = None) -> dict:
        url = f"{self._base}{path}"
        async with AsyncClient(timeout=15, verify=False) as client:
            resp = await client.patch(url, headers=await self._headers(), json=payload or {})
            if resp.status_code == 401:
                resp = await client.patch(url, headers=await self._refresh_and_headers(), json=payload or {})
            resp.raise_for_status()
            return resp.json() if resp.content else {}

    async def delete(self, path: str) -> None:
        url = f"{self._base}{path}"
        async with AsyncClient(timeout=15, verify=False) as client:
            resp = await client.delete(url, headers=await self._headers())
            if resp.status_code == 401:
                resp = await client.delete(url, headers=await self._refresh_and_headers())
            resp.raise_for_status()


class RemnawaveService(VpnPanelInterface):
    """
    High-level Remnawave API service.
    Compatible with VpnPanelInterface.
    Based on Remnawave OpenAPI v2.7.4
    """

    def __init__(self) -> None:
        from app.core.configs.remnawave_config import remnawave as _cfg
        if not _cfg.remnawave_url:
            raise RuntimeError("Remnawave not configured. Set REMNAWAVE_URL in .env")

        api_key = _cfg.remnawave_api_key.get_secret_value() if _cfg.remnawave_api_key else None
        login = _cfg.remnawave_login if not api_key else None
        password = _cfg.remnawave_password.get_secret_value() if (not api_key and _cfg.remnawave_password) else None

        if not api_key and not (login and password):
            raise RuntimeError(
                "Remnawave: задайте REMNAWAVE_API_KEY или REMNAWAVE_LOGIN + REMNAWAVE_PASSWORD"
            )

        auth_method = "API Key" if api_key else "login/password"
        log.info(f"Remnawave auth: {auth_method}")

        self._client = RemnawaveClient(
            base_url=_cfg.remnawave_url,
            login=login,
            password=password,
            api_key=api_key,
        )
        self._base_url = _cfg.remnawave_url.rstrip("/")

    # ── System ──────────────────────────────────────────────────────────────

    async def get_system_stats(self) -> dict:
        """
        GET /api/system/stats
        Returns flattened stats dict for compatibility with panel views.
        """
        data = await self._client.get("/api/system/stats")
        r = data.get("response", data)
        users = r.get("users", {})
        online_stats = r.get("onlineStats", {})
        nodes = r.get("nodes", {})

        # Format lifetime traffic
        lifetime_bytes = 0
        try:
            lifetime_bytes = int(nodes.get("totalBytesLifetime", 0) or 0)
        except (ValueError, TypeError):
            pass
        lifetime_gb = round(lifetime_bytes / 1073741824, 2) if lifetime_bytes else 0
        traffic_str = f"{lifetime_gb} GB"

        return {
            "totalUsers": users.get("totalUsers", 0),
            "onlineNow": online_stats.get("onlineNow", 0),
            "totalOnlineNodes": nodes.get("totalOnline", 0),
            "totalBytesLifetime": traffic_str,
            "statusCounts": users.get("statusCounts", {}),
            # Compatibility aliases
            "users_active": online_stats.get("onlineNow", 0),
            "total_user": users.get("totalUsers", 0),
        }

    async def get_bandwidth_stats(self) -> dict:
        """GET /api/system/stats/bandwidth"""
        data = await self._client.get("/api/system/stats/bandwidth")
        return data.get("response", data)

    async def get_nodes_stats(self) -> dict:
        """GET /api/system/stats/nodes"""
        data = await self._client.get("/api/system/stats/nodes")
        return data.get("response", data)

    async def validate_connection(self) -> bool:
        try:
            await self._client.get("/api/system/stats")
            return True
        except Exception as e:
            log.warning(f"Remnawave connection check failed: {e}")
            return False

    # ── Nodes ────────────────────────────────────────────────────────────────

    async def get_nodes(self) -> list[dict]:
        """GET /api/nodes — список всех нод"""
        try:
            data = await self._client.get("/api/nodes")
            nodes = data.get("response", data)
            return nodes if isinstance(nodes, list) else []
        except Exception as e:
            log.warning(f"Remnawave get_nodes failed: {e}")
            return []

    # ── Users ────────────────────────────────────────────────────────────────

    async def get_all_users(self, size: int = 100, start: int = 0) -> list[dict]:
        """GET /api/users?size=N&start=N"""
        try:
            data = await self._client.get("/api/users", params={"size": size, "start": start})
            r = data.get("response", data)
            users = r.get("users", r) if isinstance(r, dict) else r
            return users if isinstance(users, list) else []
        except Exception as e:
            log.warning(f"Remnawave get_all_users failed: {e}")
            return []

    async def create_user(
        self,
        username: str,
        expire_days: int = 30,
        data_limit_gb: int = 0,
        **kwargs,
    ) -> dict:
        """POST /api/users"""
        expire_at = (
            datetime.now(timezone.utc) + timedelta(days=expire_days)
        ).strftime("%Y-%m-%dT%H:%M:%S.000Z")

        payload: dict = {
            "username": username,
            "expireAt": expire_at,
            "trafficLimitBytes": data_limit_gb * 1024 ** 3 if data_limit_gb > 0 else 0,
            "trafficLimitStrategy": "NO_RESET",
            "status": "ACTIVE",
        }

        data = await self._client.post("/api/users", payload)
        response = data.get("response", data)

        # Normalize to common format
        sub_url = response.get("subscriptionUrl") or response.get("subscription_url", "")
        response["subscription_url"] = sub_url
        response["uuid"] = response.get("uuid", "")
        return response

    async def get_user(self, username: str) -> Optional[dict]:
        """GET /api/users/by-username/{username}"""
        try:
            data = await self._client.get(f"/api/users/by-username/{username}")
            response = data.get("response", data)
            if not response:
                return None
            response["_normalized_status"] = self._normalize_status(response.get("status", ""))
            return response
        except Exception:
            return None

    async def get_user_by_uuid(self, uuid: str) -> Optional[dict]:
        """GET /api/users/{uuid}"""
        try:
            data = await self._client.get(f"/api/users/{uuid}")
            return data.get("response", data)
        except Exception:
            return None

    async def get_user_by_telegram_id(self, telegram_id: int) -> list[dict]:
        """GET /api/users/by-telegram-id/{telegramId}"""
        try:
            data = await self._client.get(f"/api/users/by-telegram-id/{telegram_id}")
            response = data.get("response", data)
            return response if isinstance(response, list) else []
        except Exception:
            return []

    async def update_user(self, uuid: str, **fields) -> dict:
        """PATCH /api/users — обновить любые поля пользователя"""
        payload = {"uuid": uuid, **fields}
        data = await self._client.patch("/api/users", payload)
        return data.get("response", data)

    async def extend_user(self, username: str, extra_days: int) -> dict:
        """Продлить подписку на extra_days дней."""
        user = await self.get_user(username)
        if not user:
            raise RuntimeError(f"Remnawave user {username} not found")

        uuid = user.get("uuid")
        current_expire = user.get("expireAt")
        now = datetime.now(timezone.utc)

        if current_expire:
            try:
                base = datetime.fromisoformat(current_expire.replace("Z", "+00:00"))
                if base < now:
                    base = now
            except Exception:
                base = now
        else:
            base = now

        new_expire = (base + timedelta(days=extra_days)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        return await self.update_user(uuid, expireAt=new_expire)

    async def disable_user(self, username: str) -> dict:
        """POST /api/users/{uuid}/actions/disable"""
        user = await self.get_user(username)
        if not user:
            return {}
        uuid = user.get("uuid")
        if not uuid:
            return {}
        try:
            data = await self._client.post(f"/api/users/{uuid}/actions/disable")
            return data.get("response", data)
        except Exception as e:
            log.warning(f"Remnawave disable_user {username} failed: {e}")
            return {}

    async def enable_user(self, username: str) -> dict:
        """POST /api/users/{uuid}/actions/enable"""
        user = await self.get_user(username)
        if not user:
            return {}
        uuid = user.get("uuid")
        if not uuid:
            return {}
        try:
            data = await self._client.post(f"/api/users/{uuid}/actions/enable")
            return data.get("response", data)
        except Exception as e:
            log.warning(f"Remnawave enable_user {username} failed: {e}")
            return {}

    async def reset_user_traffic(self, username: str) -> dict:
        """POST /api/users/{uuid}/actions/reset-traffic"""
        user = await self.get_user(username)
        if not user:
            return {}
        uuid = user.get("uuid")
        if not uuid:
            return {}
        try:
            data = await self._client.post(f"/api/users/{uuid}/actions/reset-traffic")
            return data.get("response", data)
        except Exception as e:
            log.warning(f"Remnawave reset_traffic {username} failed: {e}")
            return {}

    async def delete_user(self, username: str) -> None:
        """DELETE /api/users/{uuid}"""
        user = await self.get_user(username)
        if not user:
            return
        uuid = user.get("uuid")
        if uuid:
            try:
                await self._client.delete(f"/api/users/{uuid}")
            except Exception as e:
                log.warning(f"Remnawave delete_user {username} failed: {e}")

    async def get_subscription_info(self, short_uuid: str) -> Optional[dict]:
        """GET /api/sub/{shortUuid}/info — публичный эндпоинт, без авторизации"""
        try:
            async with AsyncClient(timeout=10, verify=False) as client:
                resp = await client.get(f"{self._base_url}/api/sub/{short_uuid}/info")
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("response", data)
        except Exception:
            pass
        return None

    # ── Helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _normalize_status(status: str) -> str:
        mapping = {
            "ACTIVE": "active",
            "DISABLED": "disabled",
            "LIMITED": "limited",
            "EXPIRED": "expired",
        }
        return mapping.get(status.upper() if status else "", status.lower() if status else "")
