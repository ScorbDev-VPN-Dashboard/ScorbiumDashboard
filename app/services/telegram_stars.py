from typing import Optional
from aiogram import Bot
from aiogram.types import LabeledPrice

from app.utils.log import log


class TelegramStarsService:
    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    async def send_invoice(
        self,
        chat_id: int,
        title: str,
        description: str,
        payload: str,
        stars_amount: int,
    ) -> bool:
        try:
            await self.bot.send_invoice(
                chat_id=chat_id,
                title=title,
                description=description,
                payload=payload,
                currency="XTR", 
                prices=[LabeledPrice(label=title, amount=stars_amount)],
            )
            return True
        except Exception as e:
            log.error(f"Stars invoice error for {chat_id}: {e}")
            return False

    @staticmethod
    def rub_to_stars(rub_amount: float) -> int:
        """Конвертация рублей в Stars (примерный курс: 1 Star ≈ 1.5 RUB)."""
        return max(1, int(rub_amount / 1.5))
