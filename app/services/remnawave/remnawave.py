"""
Remnawave VPN panel service — implements VpnPanelInterface.
Uses RemnaWaveAPI (production-ready client with retry logic).
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.services.vpn_panel_interface import VpnPanelInterface
from app.services.remnawave.remnawave_api import RemnaWaveAPI, RemnaWaveAPIError, get_remnawave_api, UserStatus
from app.utils.log import log


class RemnawaveService(VpnPanelInterface):
    """
    Adapter: VpnPanelInterface → RemnaWaveAPI.
    All methods use async context manager internally.
    """

    def __init__(self) -> None:
        self._api: RemnaWaveAPI = get_remnawave_api()
        auth = "API Key" if self._api.api_key else "login/password"
        log.info(f"Remnawave auth: {auth}")

    # ── System ──────────────────────────────────────────────────────────────

    async def get_system_stats(self) -> dict:
        """Returns flattened stats dict for panel views."""
        async with self._api as api:
            r = await api.get_system_stats()

        users = r.get("users", {})
        online_stats = r.get("onlineStats", {})
        nodes = r.get("nodes", {})

        lifetime_bytes = 0
        try:
            lifetime_bytes = int(nodes.get("totalBytesLifetime", 0) or 0)
        except (ValueError, TypeError):
            pass
        traffic_str = f"{round(lifetime_bytes / 1073741824, 2)} GB"

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

    async def validate_connection(self) -> bool:
        try:
            async with self._api as api:
                await api.get_system_stats()
            return True
        except Exception as e:
            log.warning(f"Remnawave connection check failed: {e}")
            return False

    # ── Nodes ────────────────────────────────────────────────────────────────

    async def get_nodes(self) -> list[dict]:
        try:
            async with self._api as api:
                nodes = await api.get_all_nodes()
            return [
                {
                    "uuid": n.uuid,
                    "name": n.name,
                    "address": n.address,
                    "countryCode": n.country_code,
                    "isConnected": n.is_connected,
                    "isDisabled": n.is_disabled,
                    "usersOnline": n.users_online,
                    "status": "connected" if n.is_connected else ("disabled" if n.is_disabled else "error"),
                }
                for n in nodes
            ]
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
        expire_at = datetime.now(timezone.utc) + timedelta(days=expire_days)
        async with self._api as api:
            user = await api.create_user(
                username=username,
                expire_at=expire_at,
                traffic_limit_bytes=data_limit_gb * 1024 ** 3 if data_limit_gb > 0 else 0,
            )
        return {
            "uuid": user.uuid,
            "short_uuid": user.short_uuid,
            "username": user.username,
            "subscription_url": user.subscription_url,
            "subscriptionUrl": user.subscription_url,
            "status": user.status.value,
        }

    async def get_user(self, username: str) -> Optional[dict]:
        try:
            async with self._api as api:
                user = await api.get_user_by_username(username)
            if not user:
                return None
            return {
                "uuid": user.uuid,
                "username": user.username,
                "status": user.status.value,
                "subscription_url": user.subscription_url,
                "_normalized_status": self._normalize_status(user.status.value),
                "expireAt": user.expire_at.isoformat() if user.expire_at else None,
                "trafficLimitBytes": user.traffic_limit_bytes,
                "userTraffic": {
                    "usedTrafficBytes": user.used_traffic_bytes,
                    "lifetimeUsedTrafficBytes": user.lifetime_used_traffic_bytes,
                    "onlineAt": user.online_at.isoformat() if user.online_at else None,
                } if user.user_traffic else None,
            }
        except Exception:
            return None

    async def get_user_by_uuid(self, uuid: str) -> Optional[dict]:
        try:
            async with self._api as api:
                user = await api.get_user_by_uuid(uuid)
            if not user:
                return None
            return {"uuid": user.uuid, "username": user.username, "status": user.status.value}
        except Exception:
            return None

    async def get_all_users(self, size: int = 100, start: int = 0) -> list[dict]:
        try:
            async with self._api as api:
                result = await api.get_all_users(start=start, size=size)
            users = []
            for u in result.get("users", []):
                users.append({
                    "uuid": u.uuid,
                    "username": u.username,
                    "status": u.status.value,
                    "subscriptionUrl": u.subscription_url,
                    "expireAt": u.expire_at.isoformat() if u.expire_at else None,
                    "trafficLimitBytes": u.traffic_limit_bytes,
                    "userTraffic": {
                        "usedTrafficBytes": u.used_traffic_bytes,
                        "lifetimeUsedTrafficBytes": u.lifetime_used_traffic_bytes,
                        "onlineAt": u.online_at.isoformat() if u.online_at else None,
                    } if u.user_traffic else None,
                })
            return users
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
                base = datetime.fromisoformat(current_expire.replace("Z", "+00:00"))
                if base < now:
                    base = now
            except Exception:
                base = now
        else:
            base = now

        new_expire = (base + timedelta(days=extra_days)).isoformat()
        async with self._api as api:
            user = await api.update_user(uuid, expireAt=new_expire)
        return {"uuid": user.uuid, "status": user.status.value}

    async def disable_user(self, username: str) -> dict:
        user_dict = await self.get_user(username)
        if not user_dict:
            return {}
        uuid = user_dict.get("uuid")
        if not uuid:
            return {}
        try:
            async with self._api as api:
                user = await api.disable_user(uuid)
            return {"uuid": user.uuid, "status": user.status.value}
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
            async with self._api as api:
                user = await api.enable_user(uuid)
            return {"uuid": user.uuid, "status": user.status.value}
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
                async with self._api as api:
                    await api.delete_user(uuid)
            except Exception as e:
                log.warning(f"Remnawave delete_user {username} failed: {e}")

    async def get_user_by_telegram_id(self, telegram_id: int) -> list[dict]:
        try:
            async with self._api as api:
                users = await api.get_user_by_telegram_id(telegram_id)
            return [{"uuid": u.uuid, "username": u.username, "status": u.status.value} for u in users]
        except Exception:
            return []

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
