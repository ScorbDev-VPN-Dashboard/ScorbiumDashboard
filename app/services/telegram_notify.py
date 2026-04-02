from typing import Optional
import httpx

from app.core.config import config
from app.utils.log import log


class TelegramNotifyService:
    def __init__(self) -> None:
        self._token = config.telegram.telegram_bot_token.get_secret_value()
        self._base = f"https://api.telegram.org/bot{self._token}"

    async def send_message(
        self,
        chat_id: int,
        text: str,
        parse_mode: str = "HTML",
        disable_notification: bool = False,
    ) -> bool:
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_notification": disable_notification,
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(f"{self._base}/sendMessage", json=payload)
                if resp.status_code == 200:
                    return True
                log.warning(f"Telegram send failed for {chat_id}: {resp.text}")
                return False
        except Exception as e:
            log.error(f"Telegram notify error for {chat_id}: {e}")
            return False

    async def broadcast(
        self,
        user_ids: list[int],
        text: str,
        parse_mode: str = "HTML",
    ) -> tuple[int, int]:
        """Returns (sent_count, failed_count)."""
        sent, failed = 0, 0
        for uid in user_ids:
            ok = await self.send_message(uid, text, parse_mode)
            if ok:
                sent += 1
            else:
                failed += 1
        return sent, failed

    async def get_bot_info(self) -> Optional[dict]:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{self._base}/getMe")
                if resp.status_code == 200:
                    return resp.json().get("result")
        except Exception as e:
            log.error(f"getMe failed: {e}")
        return None
