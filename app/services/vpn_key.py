from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import config
from app.models.plan import Plan
from app.models.vpn_key import VpnKey, VpnKeyStatus
from app.services.pasarguard.pasarguard import get_vpn_panel
from app.services.vpn_panel_interface import VpnPanelInterface
from app.utils.log import log


def _marzban_username(user_id: int, key_id: int) -> str:
    return f"vpn_{user_id}_{key_id}"


class VpnKeyService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self._marzban: Optional[VpnPanelInterface] = None

    def _get_panel(self) -> VpnPanelInterface:
        """Lazy init VPN panel — не падает при старте если панель не сконфигурирована."""
        if self._marzban is None:
            self._marzban = get_vpn_panel()
        return self._marzban

    async def get_by_id(self, key_id: int) -> Optional[VpnKey]:
        result = await self.session.execute(select(VpnKey).where(VpnKey.id == key_id))
        return result.scalar_one_or_none()

    async def get_active_for_user(self, user_id: int) -> list[VpnKey]:
        result = await self.session.execute(
            select(VpnKey)
            .where(
                VpnKey.user_id == user_id,
                VpnKey.status == VpnKeyStatus.ACTIVE.value,
            )
            .order_by(VpnKey.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_user_keys(self, user_id: int) -> list[VpnKey]:
        return await self.get_active_for_user(user_id)

    async def get_all_for_user(self, user_id: int) -> list[VpnKey]:
        result = await self.session.execute(
            select(VpnKey)
            .where(VpnKey.user_id == user_id)
            .order_by(VpnKey.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_all(self, limit: int = 200) -> list[VpnKey]:
        result = await self.session.execute(
            select(VpnKey).order_by(VpnKey.id.desc()).limit(limit)
        )
        return list(result.scalars().all())

    async def count_active(self) -> int:
        result = await self.session.execute(
            select(func.count()).where(VpnKey.status == VpnKeyStatus.ACTIVE.value)
        )
        return result.scalar_one()

    async def provision(self, user_id: int, plan: Plan) -> Optional[VpnKey]:

        from app.services.bot_settings import BotSettingsService

        async with AsyncSessionFactory() as check_session:
            if await BotSettingsService(check_session).is_mute_all_enabled():
                return None

        expires_at = datetime.now(timezone.utc) + timedelta(days=plan.duration_days)
        key = VpnKey(
            user_id=user_id,
            plan_id=plan.id,
            price=plan.price,
            expires_at=expires_at,
            name=f"{plan.name} — {plan.duration_days} дн.",
            status=VpnKeyStatus.ACTIVE.value,
            access_url="pending",
        )
        self.session.add(key)
        await self.session.flush()

        username = _marzban_username(user_id, key.id)

        try:
            import json as _json

            from app.services.bot_settings import BotSettingsService

            group_ids: list[int] = []
            try:
                raw_groups = await BotSettingsService(self.session).get("vpn_group_ids")
                if raw_groups:
                    group_ids = [
                        int(x)
                        for x in _json.loads(raw_groups)
                        if str(x).strip().isdigit()
                    ]
            except Exception as e:
                log.warning(f"Failed to load vpn_group_ids setting: {e}")
                group_ids = []

            marz_user = await self._get_panel().create_user(
                username=username,
                expire_days=plan.duration_days,
                data_limit_gb=0,
                group_ids=group_ids or None,
            )
        except Exception as e:
            log.error(f"Marzban/Pasarguard create_user failed for {username}: {e}")
            await self.session.delete(key)
            await self.session.flush()
            return None

        sub_token = marz_user.get("subscription_url", "")
        # Build access URL
        _pg = config.pasarguard
        panel_base = str(_pg.pasarguard_admin_panel).rstrip("/") if _pg else ""

        if sub_token:
            if sub_token.startswith("http"):
                access_url = sub_token.rstrip("/")
            else:
                access_url = f"{panel_base}{sub_token.rstrip('/')}"
        else:
            access_url = f"{panel_base}/sub/{username}"

        key.pasarguard_key_id = username
        key.access_url = access_url
        await self.session.flush()
        log.info(f"✅ VPN provisioned: user={user_id} key={key.id} marzban={username}")
        return key

    async def provision_for_subscription(
        self, user_id: int, subscription_id: int, plan: Plan
    ) -> Optional[VpnKey]:
        return await self.provision(user_id, plan)

    # ── Management ───────────────────────────────────────────────────────────

    async def revoke(self, key_id: int) -> Optional[VpnKey]:
        key = await self.get_by_id(key_id)
        if not key:
            return None
        if key.pasarguard_key_id:
            try:
                await self._get_panel().disable_user(key.pasarguard_key_id)
            except Exception as e:
                log.warning(f"Marzban disable failed: {e}")
        key.status = VpnKeyStatus.REVOKED.value
        await self.session.flush()
        return key

    async def extend(self, key_id: int, days: int) -> Optional[VpnKey]:
        key = await self.get_by_id(key_id)
        if not key:
            return None
        if key.expires_at:
            key.expires_at = key.expires_at + timedelta(days=days)
        else:
            key.expires_at = datetime.now(timezone.utc) + timedelta(days=days)
        key.status = VpnKeyStatus.ACTIVE.value
        if key.pasarguard_key_id:
            try:
                await self._get_panel().extend_user(key.pasarguard_key_id, days)
            except Exception as e:
                log.warning(f"Marzban extend failed: {e}")
        await self.session.flush()
        return key

    async def delete_from_marzban(self, key_id: int) -> Optional[VpnKey]:
        key = await self.get_by_id(key_id)
        if not key:
            return None
        if key.pasarguard_key_id:
            try:
                await self._get_panel().delete_user(key.pasarguard_key_id)
            except Exception as e:
                log.warning(f"Marzban delete failed: {e}")
        key.status = VpnKeyStatus.REVOKED.value
        await self.session.flush()
        return key

    async def revoke_all_for_user(self, user_id: int) -> int:
        keys = await self.get_active_for_user(user_id)
        for key in keys:
            if key.pasarguard_key_id:
                try:
                    await self._get_panel().disable_user(key.pasarguard_key_id)
                except Exception:
                    pass
            key.status = VpnKeyStatus.REVOKED.value
        await self.session.flush()
        return len(keys)

    async def sync_from_marzban(self) -> dict:
        synced, errors = 0, 0
        result = await self.session.execute(
            select(VpnKey).where(
                VpnKey.status == VpnKeyStatus.ACTIVE.value,
                VpnKey.pasarguard_key_id.isnot(None),
            )
        )
        for key in result.scalars().all():
            try:
                marz_user = await self._get_panel().get_user(key.pasarguard_key_id)
                if not marz_user:
                    key.status = VpnKeyStatus.REVOKED.value
                else:
                    # Support both active/disabled/expired statuses
                    raw_status = (
                        marz_user.get("_normalized_status")
                        or marz_user.get("status", "")
                    ).lower()
                    if raw_status in ("expired", "limited", "disabled"):
                        key.status = VpnKeyStatus.EXPIRED.value
                synced += 1
            except Exception as e:
                log.warning(f"Sync error key {key.id}: {e}")
                errors += 1
        await self.session.flush()
        return {"synced": synced, "errors": errors}

    async def expire_outdated(self) -> int:
        now = datetime.now(timezone.utc)
        result = await self.session.execute(
            select(VpnKey).where(
                VpnKey.status == VpnKeyStatus.ACTIVE.value,
                VpnKey.expires_at < now,
            )
        )
        keys = list(result.scalars().all())
        for key in keys:
            key.status = VpnKeyStatus.EXPIRED.value
            if key.pasarguard_key_id:
                try:
                    await self._get_panel().disable_user(key.pasarguard_key_id)
                except Exception:
                    pass
        await self.session.flush()
        return len(keys)
