"""
Remnawave VPN panel service — implements VpnPanelInterface.
Uses official Remnawave Python SDK (remnawave package).
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.services.vpn_panel_interface import VpnPanelInterface
from app.utils.log import log


def _get_sdk():
    """Создаём экземпляр официального Remnawave SDK."""
    from app.core.configs.remnawave_config import remnawave as _cfg
    if not _cfg or not _cfg.remnawave_url:
        raise RuntimeError("Remnawave not configured. Set REMNAWAVE_URL in .env")

    api_key = _cfg.remnawave_api_key.get_secret_value() if _cfg.remnawave_api_key else None
    if not api_key:
        raise RuntimeError("Remnawave: задайте REMNAWAVE_API_KEY (Settings → API Tokens в панели)")

    from remnawave import RemnawaveSDK
    return RemnawaveSDK(base_url=_cfg.remnawave_url, token=api_key)


class RemnawaveService(VpnPanelInterface):
    """
    Adapter: VpnPanelInterface → официальный Remnawave Python SDK.
    """

    def __init__(self) -> None:
        self._sdk = _get_sdk()
        log.info("Remnawave: using official Python SDK")

    # ── System ──────────────────────────────────────────────────────────────

    async def get_system_stats(self) -> dict:
        try:
            r = await self._sdk.system.get_stats()
            users = getattr(r, "users", None) or {}
            online_stats = getattr(r, "onlineStats", None) or getattr(r, "online_stats", None) or {}
            nodes = getattr(r, "nodes", None) or {}

            if hasattr(users, "__dict__"):
                total_users = getattr(users, "totalUsers", 0) or getattr(users, "total_users", 0)
                status_counts = getattr(users, "statusCounts", {}) or {}
            else:
                total_users = users.get("totalUsers", 0) if isinstance(users, dict) else 0
                status_counts = users.get("statusCounts", {}) if isinstance(users, dict) else {}

            if hasattr(online_stats, "__dict__"):
                online_now = getattr(online_stats, "onlineNow", 0) or getattr(online_stats, "online_now", 0)
            else:
                online_now = online_stats.get("onlineNow", 0) if isinstance(online_stats, dict) else 0

            if hasattr(nodes, "__dict__"):
                total_online = getattr(nodes, "totalOnline", 0) or getattr(nodes, "total_online", 0)
                lifetime_bytes = getattr(nodes, "totalBytesLifetime", 0) or 0
            else:
                total_online = nodes.get("totalOnline", 0) if isinstance(nodes, dict) else 0
                lifetime_bytes = nodes.get("totalBytesLifetime", 0) if isinstance(nodes, dict) else 0

            try:
                lifetime_gb = round(int(lifetime_bytes) / 1073741824, 2)
            except (ValueError, TypeError):
                lifetime_gb = 0

            return {
                "totalUsers": total_users,
                "onlineNow": online_now,
                "totalOnlineNodes": total_online,
                "totalBytesLifetime": f"{lifetime_gb} GB",
                "statusCounts": status_counts if isinstance(status_counts, dict) else {},
                "users_active": online_now,
                "total_user": total_users,
            }
        except Exception as e:
            log.error(f"Remnawave get_system_stats failed: {e}")
            raise

    async def validate_connection(self) -> bool:
        try:
            await self._sdk.system.get_stats()
            return True
        except Exception as e:
            log.warning(f"Remnawave connection check failed: {e}")
            return False

    # ── Nodes ────────────────────────────────────────────────────────────────

    async def get_nodes(self) -> list[dict]:
        try:
            nodes = await self._sdk.nodes.get_all_nodes()
            result = []
            for n in (nodes if isinstance(nodes, list) else getattr(nodes, "response", [])):
                result.append({
                    "uuid": getattr(n, "uuid", ""),
                    "name": getattr(n, "name", ""),
                    "address": getattr(n, "address", ""),
                    "countryCode": getattr(n, "countryCode", getattr(n, "country_code", "")),
                    "isConnected": getattr(n, "isConnected", getattr(n, "is_connected", False)),
                    "isDisabled": getattr(n, "isDisabled", getattr(n, "is_disabled", False)),
                    "usersOnline": getattr(n, "usersOnline", getattr(n, "users_online", 0)),
                    "status": "connected" if getattr(n, "isConnected", False) else "error",
                })
            return result
        except Exception as e:
            log.warning(f"Remnawave get_nodes failed: {e}")
            return []

    # ── Users ────────────────────────────────────────────────────────────────

    async def create_user(
        self,
        username: str,
        expire_days: int = 30,
        data_limit_gb: int = 0,
        **kwargs,
    ) -> dict:
        from remnawave.models import CreateUserRequestDto
        expire_at = (datetime.now(timezone.utc) + timedelta(days=expire_days)).isoformat()
        req = CreateUserRequestDto(
            username=username,
            expireAt=expire_at,
            trafficLimitBytes=data_limit_gb * 1024 ** 3 if data_limit_gb > 0 else 0,
            trafficLimitStrategy="NO_RESET",
            status="ACTIVE",
        )
        r = await self._sdk.users.create_user(req)
        user = getattr(r, "response", r)
        sub_url = getattr(user, "subscriptionUrl", getattr(user, "subscription_url", ""))
        return {
            "uuid": getattr(user, "uuid", ""),
            "short_uuid": getattr(user, "shortUuid", getattr(user, "short_uuid", "")),
            "username": getattr(user, "username", username),
            "subscription_url": sub_url,
            "subscriptionUrl": sub_url,
            "status": getattr(user, "status", "ACTIVE"),
        }

    async def get_user(self, username: str) -> Optional[dict]:
        try:
            r = await self._sdk.users.get_user_by_username(username)
            user = getattr(r, "response", r)
            if not user:
                return None
            status = str(getattr(user, "status", "ACTIVE"))
            traffic = getattr(user, "userTraffic", getattr(user, "user_traffic", None))
            return {
                "uuid": getattr(user, "uuid", ""),
                "username": getattr(user, "username", username),
                "status": status,
                "subscription_url": getattr(user, "subscriptionUrl", getattr(user, "subscription_url", "")),
                "_normalized_status": self._normalize_status(status),
                "expireAt": str(getattr(user, "expireAt", getattr(user, "expire_at", ""))),
                "trafficLimitBytes": getattr(user, "trafficLimitBytes", 0),
                "userTraffic": {
                    "usedTrafficBytes": getattr(traffic, "usedTrafficBytes", 0) if traffic else 0,
                    "lifetimeUsedTrafficBytes": getattr(traffic, "lifetimeUsedTrafficBytes", 0) if traffic else 0,
                    "onlineAt": str(getattr(traffic, "onlineAt", "")) if traffic else None,
                } if traffic else None,
            }
        except Exception:
            return None

    async def get_user_by_uuid(self, uuid: str) -> Optional[dict]:
        try:
            r = await self._sdk.users.get_user_by_uuid(uuid)
            user = getattr(r, "response", r)
            if not user:
                return None
            return {
                "uuid": getattr(user, "uuid", uuid),
                "username": getattr(user, "username", ""),
                "status": str(getattr(user, "status", "")),
            }
        except Exception:
            return None

    async def get_all_users(self, size: int = 100, start: int = 0) -> list[dict]:
        try:
            r = await self._sdk.users.get_all_users()
            users_list = getattr(r, "users", []) or []
            result = []
            for u in users_list:
                traffic = getattr(u, "userTraffic", getattr(u, "user_traffic", None))
                sub_url = getattr(u, "subscriptionUrl", getattr(u, "subscription_url", ""))
                expire = getattr(u, "expireAt", getattr(u, "expire_at", None))
                result.append({
                    "uuid": getattr(u, "uuid", ""),
                    "username": getattr(u, "username", ""),
                    "status": str(getattr(u, "status", "")),
                    "subscriptionUrl": sub_url,
                    "expireAt": str(expire) if expire else None,
                    "trafficLimitBytes": getattr(u, "trafficLimitBytes", 0),
                    "userTraffic": {
                        "usedTrafficBytes": getattr(traffic, "usedTrafficBytes", 0) if traffic else 0,
                        "lifetimeUsedTrafficBytes": getattr(traffic, "lifetimeUsedTrafficBytes", 0) if traffic else 0,
                        "onlineAt": str(getattr(traffic, "onlineAt", "")) if traffic else None,
                    } if traffic else None,
                })
            return result
        except Exception as e:
            log.warning(f"Remnawave get_all_users failed: {e}")
            return []

    async def extend_user(self, username: str, extra_days: int) -> dict:
        user_dict = await self.get_user(username)
        if not user_dict:
            raise RuntimeError(f"Remnawave user {username} not found")

        uuid = user_dict.get("uuid")
        current_expire = user_dict.get("expireAt")
        now = datetime.now(timezone.utc)

        if current_expire:
            try:
                base = datetime.fromisoformat(str(current_expire).replace("Z", "+00:00"))
                if base < now:
                    base = now
            except Exception:
                base = now
        else:
            base = now

        new_expire = (base + timedelta(days=extra_days)).isoformat()

        from remnawave.models import UpdateUserRequestDto
        req = UpdateUserRequestDto(uuid=uuid, expireAt=new_expire)
        r = await self._sdk.users.update_user(req)
        user = getattr(r, "response", r)
        return {"uuid": getattr(user, "uuid", uuid), "status": str(getattr(user, "status", ""))}

    async def disable_user(self, username: str) -> dict:
        user_dict = await self.get_user(username)
        if not user_dict:
            return {}
        uuid = user_dict.get("uuid")
        if not uuid:
            return {}
        try:
            r = await self._sdk.users.disable_user(uuid)
            user = getattr(r, "response", r)
            return {"uuid": getattr(user, "uuid", uuid), "status": str(getattr(user, "status", ""))}
        except Exception as e:
            log.warning(f"Remnawave disable_user {username} failed: {e}")
            return {}

    async def enable_user(self, username: str) -> dict:
        user_dict = await self.get_user(username)
        if not user_dict:
            return {}
        uuid = user_dict.get("uuid")
        if not uuid:
            return {}
        try:
            r = await self._sdk.users.enable_user(uuid)
            user = getattr(r, "response", r)
            return {"uuid": getattr(user, "uuid", uuid), "status": str(getattr(user, "status", ""))}
        except Exception as e:
            log.warning(f"Remnawave enable_user {username} failed: {e}")
            return {}

    async def delete_user(self, username: str) -> None:
        user_dict = await self.get_user(username)
        if not user_dict:
            return
        uuid = user_dict.get("uuid")
        if uuid:
            try:
                await self._sdk.users.delete_user(uuid)
            except Exception as e:
                log.warning(f"Remnawave delete_user {username} failed: {e}")

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
