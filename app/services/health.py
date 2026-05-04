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
            ("vpn_panel", self._check_vpn_panel),
            ("payment_systems", self._check_payment_systems),
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
        """Send Telegram alerts for down services, respecting notification settings."""
        from app.core.database import AsyncSessionFactory
        from app.services.bot_settings import BotSettingsService

        async with AsyncSessionFactory() as session:
            settings = BotSettingsService(session)
            if not (await settings.get("notify_monitoring_enabled")) == "1":
                return

            cooldown_sec = int((await settings.get("notify_cooldown_seconds")) or "300")
            notify_on_degraded = (await settings.get("notify_on_degraded")) == "1"
            chat_ids_raw = await settings.get("notify_chat_ids")
            notify_svc = {
                "database": (await settings.get("notify_svc_database")) == "1",
                "telegram_bot": (await settings.get("notify_svc_telegram_bot")) == "1",
                "vpn_panel": (await settings.get("notify_svc_vpn_panel")) == "1",
                "payment_systems": (await settings.get("notify_svc_yookassa")) == "1",
            }

        if chat_ids_raw and chat_ids_raw.strip():
            try:
                target_ids = [int(x.strip()) for x in chat_ids_raw.split(",") if x.strip()]
            except ValueError:
                target_ids = config.telegram.telegram_admin_ids
        else:
            target_ids = config.telegram.telegram_admin_ids

        notify = TelegramNotifyService()
        now = time.time()

        for name, entry in self._entries.items():
            if entry.status == ServiceStatus.DOWN or (notify_on_degraded and entry.status == ServiceStatus.DEGRADED):
                if not notify_svc.get(name, True):
                    continue
                cooldown = self._alert_cooldowns.get(name, 0)
                if now - cooldown < cooldown_sec:
                    continue
                self._alert_cooldowns[name] = now

                emoji = "🚨" if entry.status == ServiceStatus.DOWN else "⚠️"
                label = "недоступен" if entry.status == ServiceStatus.DOWN else "работает с перебоями"
                msg = (
                    f"{emoji} <b>Сервис {label}: {name}</b>\n\n"
                    f"Ошибка: {entry.message}\n"
                    f"Время: {entry.checked_at.strftime('%H:%M:%S')}"
                )
                for chat_id in target_ids:
                    try:
                        await notify.send_message(chat_id, msg)
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

    async def _check_vpn_panel(self, entry: HealthEntry):
        """Check Pasarguard/Marzban VPN panel (single unified check)."""
        import httpx

        base = str(config.pasarguard.pasarguard_admin_panel).rstrip("/")
        if not base or "..." in base:
            entry.warn(0, "Not configured")
            return
        start = time.time()
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{base}/api/health")
            latency = (time.time() - start) * 1000
            if resp.status_code == 200:
                entry.ok(latency, "Pasarguard/Marzban OK")
            else:
                entry.warn(latency, f"Status {resp.status_code}")
        except Exception as e:
            entry.fail(str(e))

    async def _check_payment_systems(self, entry: HealthEntry):
        """Check all configured payment systems as a single unified check."""
        import httpx

        results = []
        any_ok = False

        # YooKassa
        shop_id = config.yookassa.yookassa_shop_id
        if shop_id:
            start = time.time()
            try:
                async with httpx.AsyncClient(timeout=5) as client:
                    resp = await client.get(
                        "https://api.yookassa.ru/v3/me",
                        auth=(str(shop_id), config.yookassa.yookassa_secret_key.get_secret_value()),
                    )
                latency = (time.time() - start) * 1000
                if resp.status_code in (200, 401):
                    results.append(("YooKassa", "ok", latency))
                    any_ok = True
                else:
                    results.append(("YooKassa", "warn", latency, resp.status_code))
            except Exception as e:
                results.append(("YooKassa", "fail", 0, str(e)))

        # CryptoBot
        from app.core.database import AsyncSessionFactory
        from app.services.bot_settings import BotSettingsService

        async with AsyncSessionFactory() as session:
            cb_token = await BotSettingsService(session).get("cryptobot_token")
        if cb_token:
            start = time.time()
            async with httpx.AsyncClient(timeout=5) as client:
                try:
                    resp = await client.post(
                        "https://pay.crypt.bot/api/getMe",
                        headers={"Crypto-Pay-API-Token": cb_token},
                    )
                    latency = (time.time() - start) * 1000
                    if resp.status_code == 200:
                        results.append(("CryptoBot", "ok", latency))
                        any_ok = True
                    else:
                        results.append(("CryptoBot", "warn", latency, resp.status_code))
                except Exception as e:
                    results.append(("CryptoBot", "fail", 0, str(e)))

        # FreeKassa (basic DNS/connectivity check)
        fk_shop = ""
        async with AsyncSessionFactory() as session:
            fk_shop = await BotSettingsService(session).get("freekassa_shop_id") or ""
        if fk_shop:
            start = time.time()
            async with httpx.AsyncClient(timeout=5) as client:
                try:
                    resp = await client.get("https://api.freekassa.com", timeout=5)
                    latency = (time.time() - start) * 1000
                    if resp.status_code < 500:
                        results.append(("FreeKassa", "ok", latency))
                        any_ok = True
                    else:
                        results.append(("FreeKassa", "warn", latency, resp.status_code))
                except Exception as e:
                    results.append(("FreeKassa", "fail", 0, str(e)))

        if not results:
            entry.warn(0, "Нет настроенных платежных систем")
            return

        failed = [r for r in results if r[1] == "fail"]
        warned = [r for r in results if r[1] == "warn"]

        if failed and not any_ok:
            msgs = [f"{r[0]}: {r[3]}" for r in failed]
            entry.fail("; ".join(msgs))
        elif failed or warned:
            details = []
            for r in results:
                if r[1] == "ok":
                    details.append(f"{r[0]} OK")
                elif r[1] == "warn":
                    details.append(f"{r[0]} {r[3]}")
                else:
                    details.append(f"{r[0]} err")
            entry.warn(0, "; ".join(details))
        else:
            entry.ok(0, "Все системы OK")


health_service = HealthService()
