"""Health check service — monitors all external dependencies."""
import asyncio
import time
from datetime import datetime, timezone
from typing import Optional

from app.core.config import config
from app.core.database import AsyncSessionFactory
from app.services.telegram_notify import TelegramNotifyService
from app.utils.log import log


class ServiceStatus:
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    DOWN = "down"


class HealthEntry:
    def __init__(self, name: str):
        self.name = name
        self.status = ServiceStatus.DOWN
        self.latency_ms = 0
        self.message = ""
        self.checked_at = datetime.now(timezone.utc)

    def ok(self, latency_ms: float = 0, message: str = "OK"):
        self.status = ServiceStatus.HEALTHY
        self.latency_ms = latency_ms
        self.message = message
        self.checked_at = datetime.now(timezone.utc)

    def warn(self, latency_ms: float = 0, message: str = ""):
        self.status = ServiceStatus.DEGRADED
        self.latency_ms = latency_ms
        self.message = message
        self.checked_at = datetime.now(timezone.utc)

    def fail(self, message: str = ""):
        self.status = ServiceStatus.DOWN
        self.latency_ms = 0
        self.message = message
        self.checked_at = datetime.now(timezone.utc)


class HealthService:
    """Checks all services and caches results."""

    def __init__(self):
        self._entries: dict[str, HealthEntry] = {}
        self._last_check = 0
        self._cache_ttl = 30  # seconds
        self._alert_cooldowns: dict[str, float] = {}
        self._alert_cooldown = 300  # 5 min between same alerts

    async def check_all(self) -> dict[str, HealthEntry]:
        """Run all health checks concurrently."""
        now = time.time()
        if now - self._last_check < self._cache_ttl and self._entries:
            return self._entries

        checks = [
            ("database", self._check_db),
            ("telegram_bot", self._check_telegram),
            ("marzban", self._check_marzban),
            ("pasarguard", self._check_pasarguard),
            ("yookassa", self._check_yookassa),
            ("cryptobot", self._check_cryptobot),
        ]

        # Run all checks concurrently with individual timeouts
        async def _safe_check(name, fn):
            entry = HealthEntry(name)
            try:
                await asyncio.wait_for(fn(entry), timeout=10.0)
            except asyncio.TimeoutError:
                entry.fail("Timeout (>10s)")
            except Exception as e:
                entry.fail(str(e))
            return name, entry

        results = await asyncio.gather(
            *[_safe_check(name, fn) for name, fn in checks],
            return_exceptions=False,
        )
        self._entries = dict(results)
        self._last_check = now
        return self._entries

    async def get_entry(self, name: str) -> Optional[HealthEntry]:
        entries = await self.check_all()
        return entries.get(name)

    def is_healthy(self) -> bool:
        critical = {"database", "telegram_bot"}
        for name, entry in self._entries.items():
            if name in critical and entry.status != ServiceStatus.HEALTHY:
                return False
        return True

    async def send_alerts(self):
        """Send Telegram alerts for down services."""
        now = time.time()
        notify = TelegramNotifyService()
        admin_ids = config.telegram.telegram_admin_ids

        for name, entry in self._entries.items():
            if entry.status == ServiceStatus.DOWN:
                cooldown = self._alert_cooldowns.get(name, 0)
                if now - cooldown < self._alert_cooldown:
                    continue
                self._alert_cooldowns[name] = now
                msg = (
                    f"🚨 <b>Сервис недоступен: {name}</b>\n\n"
                    f"Ошибка: {entry.message}\n"
                    f"Время: {entry.checked_at.strftime('%H:%M:%S')}"
                )
                for admin_id in admin_ids:
                    try:
                        await notify.send_message(admin_id, msg)
                    except Exception:
                        pass

    # ── Individual checks ──

    async def _check_db(self, entry: HealthEntry):
        from sqlalchemy import text
        start = time.time()
        async with AsyncSessionFactory() as session:
            result = await session.execute(text("SELECT 1"))
            val = result.scalar()
        latency = (time.time() - start) * 1000
        if val == 1:
            if latency > 500:
                entry.warn(latency, f"Slow response: {latency:.0f}ms")
            else:
                entry.ok(latency)
        else:
            entry.fail("Unexpected result")

    async def _check_telegram(self, entry: HealthEntry):
        from aiogram import Bot
        from aiogram.enums import ParseMode
        from aiogram.client.default import DefaultBotProperties

        start = time.time()
        bot = Bot(
            token=config.telegram.telegram_bot_token.get_secret_value(),
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        try:
            me = await bot.get_me()
            latency = (time.time() - start) * 1000
            entry.ok(latency, f"Bot: @{me.username}")
        except Exception as e:
            entry.fail(str(e))
        finally:
            await bot.session.close()

    async def _check_marzban(self, entry: HealthEntry):
        import httpx

        url = config.pasarguard.marz_base_url
        if not url:
            entry.warn(0, "Not configured")
            return
        url = url.rstrip("/")
        start = time.time()
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{url}/api/system")
            latency = (time.time() - start) * 1000
            if resp.status_code == 200:
                entry.ok(latency)
            else:
                entry.warn(latency, f"Status {resp.status_code}")
        except Exception as e:
            entry.fail(str(e))

    async def _check_pasarguard(self, entry: HealthEntry):
        import httpx

        base = config.pasarguard.pasarguard_base_url
        if not base:
            entry.warn(0, "Not configured")
            return
        start = time.time()
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{base}/api/health")
            latency = (time.time() - start) * 1000
            if resp.status_code == 200:
                entry.ok(latency)
            else:
                entry.warn(latency, f"Status {resp.status_code}")
        except Exception as e:
            entry.fail(str(e))

    async def _check_yookassa(self, entry: HealthEntry):
        import httpx

        shop_id = config.yookassa.yookassa_shop_id
        if not shop_id:
            entry.warn(0, "Not configured")
            return
        start = time.time()
        async with httpx.AsyncClient(timeout=5) as client:
            try:
                resp = await client.get(
                    "https://api.yookassa.ru/v3/me",
                    auth=(str(shop_id), config.yookassa.yookassa_secret_key.get_secret_value()),
                )
                latency = (time.time() - start) * 1000
                if resp.status_code in (200, 401):
                    entry.ok(latency, "API reachable")
                else:
                    entry.warn(latency, f"Status {resp.status_code}")
            except Exception as e:
                entry.fail(str(e))

    async def _check_cryptobot(self, entry: HealthEntry):
        from app.core.database import AsyncSessionFactory
        from app.services.bot_settings import BotSettingsService

        async with AsyncSessionFactory() as session:
            token = await BotSettingsService(session).get("cryptobot_token")
        if not token:
            entry.warn(0, "Not configured")
            return
        import httpx

        start = time.time()
        async with httpx.AsyncClient(timeout=5) as client:
            try:
                resp = await client.post(
                    "https://pay.crypt.bot/api/getMe",
                    headers={"Crypto-Pay-API-Token": token},
                )
                latency = (time.time() - start) * 1000
                if resp.status_code == 200:
                    entry.ok(latency)
                else:
                    entry.warn(latency, f"Status {resp.status_code}")
            except Exception as e:
                entry.fail(str(e))


health_service = HealthService()
