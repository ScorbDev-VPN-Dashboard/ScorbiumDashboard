"""
FreeKassa payment service.
Docs: https://docs.freekassa.com/
"""
import hashlib
import hmac
import httpx
from typing import Optional
from app.utils.log import log


class FreeKassaService:
    BASE_URL = "https://api.freekassa.com/v1"

    def __init__(self, shop_id: str, api_key: str, secret_word_1: str = "", secret_word_2: str = "") -> None:
        self._shop_id = shop_id
        self._api_key = api_key
        self._secret_word_1 = secret_word_1
        self._secret_word_2 = secret_word_2

    async def _request(self, method: str, endpoint: str, **kwargs) -> Optional[dict]:
        url = f"{self.BASE_URL}/{endpoint}"
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.request(method, url, **kwargs)
                data = resp.json()
                return data
        except Exception as e:
            log.error(f"FreeKassa request error: {e}")
            return None

    def _sign(self, nonce: str) -> str:
        """HMAC-SHA256 подпись для API запросов."""
        msg = f"{self._shop_id}|{nonce}"
        return hmac.new(self._api_key.encode(), msg.encode(), hashlib.sha256).hexdigest()

    async def get_balance(self) -> Optional[dict]:
        """Получить баланс магазина — используется для проверки подключения."""
        import time
        nonce = str(int(time.time() * 1000))
        signature = self._sign(nonce)
        payload = {
            "shopId": int(self._shop_id),
            "nonce": nonce,
            "signature": signature,
        }
        return await self._request("POST", "balance", json=payload)

    def create_payment_url(self, order_id: str, amount: float, currency: str = "RUB", email: str = "") -> str:
        """Генерирует URL для оплаты через FreeKassa."""
        sign_str = f"{self._shop_id}:{amount}:{self._secret_word_1}:{currency}:{order_id}"
        sign = hashlib.md5(sign_str.encode()).hexdigest()
        params = (
            f"m={self._shop_id}&oa={amount}&currency={currency}"
            f"&o={order_id}&s={sign}&em={email}&lang=ru"
        )
        return f"https://pay.freekassa.com/?{params}"

    @staticmethod
    def from_settings(settings: dict) -> Optional["FreeKassaService"]:
        shop_id = (settings.get("freekassa_shop_id") or "").strip()
        api_key = (settings.get("freekassa_api_key") or "").strip()
        if not shop_id or not api_key:
            return None
        secret1 = (settings.get("freekassa_secret_word_1") or "").strip()
        secret2 = (settings.get("freekassa_secret_word_2") or "").strip()
        return FreeKassaService(shop_id, api_key, secret1, secret2)
