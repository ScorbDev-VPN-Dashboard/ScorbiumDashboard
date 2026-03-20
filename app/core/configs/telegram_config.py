import re
from typing import ClassVar, List, Literal

from app.utils.log import log
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.utils.path import env_file
from app.core.exceptions import *

class _TelegramConfig(BaseSettings):
    """
    Configuration for Telegram bot
    Parameters:
    - TELEGRAM_BOT_TOKEN: Telegram bot token from @BotFather
    - TELEGRAM_ADMIN_IDS: List of admin user IDs
    - TELEGRAM_TYPE_PROTOCOL: Protocol type (long/webhook)
    """
    
    TELEGRAM_TOKEN_PATTERN: ClassVar[re.Pattern] = re.compile(r"^\d+:[A-Za-z0-9_-]+$")

    model_config = SettingsConfigDict(
        env_file=env_file,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        frozen=True,
    )

    telegram_bot_token: SecretStr = Field(
        default=...,
        description="Telegram bot token from @BotFather",
        validation_alias="TELEGRAM_BOT_TOKEN",
    )
    telegram_admin_ids: List[int] = Field(
        default=[],
        description="List of admin user IDs",
        validation_alias="TELEGRAM_ADMIN_IDS",
    )
    telegram_type_protocol: Literal["webhook", "long"] = Field(
        default="long",
        description="Protocol type (long/webhook)",
        validation_alias="TELEGRAM_TYPE_PROTOCOL",
    )

    @field_validator("telegram_bot_token")
    @classmethod
    def validate_telegram_token(cls, v: SecretStr) -> SecretStr:
        if not cls.TELEGRAM_TOKEN_PATTERN.match(v.get_secret_value()):
            raise PasarguardValueError(
                "Invalid Telegram bot token format. Expected format: '123456:ABCdef...'"
            )
        return v

    @field_validator("telegram_admin_ids", mode="before")
    @classmethod
    def parse_admin_ids(cls, v):
        if isinstance(v, str):
            if not v:
                return []
            return [int(id.strip()) for id in re.split(r"[,\s;]+", v) if id.strip()]
        return v

telegram = None
try:
    telegram = _TelegramConfig()
    log.success("✅ Telegram config initialized successfully\n")
    log.debug(f"Telegram: {telegram}")
except Exception as e:
    log.error(f"❌ Failed to initialize Telegram config: {e} \n Error in {__file__}: {e}")
