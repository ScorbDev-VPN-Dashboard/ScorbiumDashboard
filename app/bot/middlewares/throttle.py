"""
Anti-spam / rate-limit middleware for the Telegram bot.
Uses a simple in-memory token bucket: each user gets N tokens,
refilled every REFILL_INTERVAL seconds.
"""
import time
from collections import defaultdict
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery, TelegramObject

# ── Config ────────────────────────────────────────────────────────────────────
MAX_TOKENS = 10          # max burst
REFILL_RATE = 5          # tokens per second
REFILL_INTERVAL = 1.0    # check interval
CALLBACK_COST = 1        # tokens per callback
MESSAGE_COST = 2         # tokens per message (heavier)
BLOCK_DURATION = 30      # seconds to block after exhaustion


class _Bucket:
    __slots__ = ("tokens", "last_refill", "blocked_until")

    def __init__(self) -> None:
        self.tokens: float = MAX_TOKENS
        self.last_refill: float = time.monotonic()
        self.blocked_until: float = 0.0

    def refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(MAX_TOKENS, self.tokens + elapsed * REFILL_RATE)
        self.last_refill = now

    def consume(self, cost: int) -> bool:
        """Returns True if allowed, False if rate-limited."""
        now = time.monotonic()
        if now < self.blocked_until:
            return False
        self.refill()
        if self.tokens >= cost:
            self.tokens -= cost
            return True
        # Exhausted — block user
        self.blocked_until = now + BLOCK_DURATION
        return False


class ThrottleMiddleware(BaseMiddleware):
    """
    Drops updates from users who exceed the rate limit.
    Sends a one-time warning message on first block.
    """

    def __init__(self) -> None:
        self._buckets: dict[int, _Bucket] = defaultdict(_Bucket)

    def _get_user_id(self, event: TelegramObject) -> int | None:
        if isinstance(event, (Message, CallbackQuery)):
            return event.from_user.id if event.from_user else None
        return None

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user_id = self._get_user_id(event)
        if user_id is None:
            return await handler(event, data)

        cost = MESSAGE_COST if isinstance(event, Message) else CALLBACK_COST
        bucket = self._buckets[user_id]

        if not bucket.consume(cost):
            # Rate limited — silently drop, or warn once
            if isinstance(event, Message):
                try:
                    await event.answer(
                        "⏳ Слишком много запросов. Подождите немного.",
                        disable_notification=True,
                    )
                except Exception:
                    pass
            elif isinstance(event, CallbackQuery):
                try:
                    await event.answer("⏳ Слишком быстро!", show_alert=False)
                except Exception:
                    pass
            return  # drop the update

        return await handler(event, data)
