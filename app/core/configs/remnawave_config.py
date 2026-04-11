from typing import Optional
from pydantic import Field, HttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache

from app.utils.path import env_file
from app.utils.log import log


class _RemnawaveConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=env_file,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
        frozen=True,
    )

    vpn_panel_type: str = Field(
        default="marzban",
        validation_alias="VPN_PANEL_TYPE",
    )

    remnawave_url: Optional[str] = Field(
        default=None,
        validation_alias="REMNAWAVE_URL",
    )
    remnawave_login: Optional[str] = Field(
        default=None,
        validation_alias="REMNAWAVE_LOGIN",
    )
    remnawave_password: Optional[SecretStr] = Field(
        default=None,
        validation_alias="REMNAWAVE_PASSWORD",
    )
    remnawave_api_key: Optional[SecretStr] = Field(
        default=None,
        validation_alias="REMNAWAVE_API_KEY",
    )


@lru_cache()
def get_remnawave_config() -> _RemnawaveConfig:
    return _RemnawaveConfig()


try:
    remnawave = get_remnawave_config()
    log.info(f"✅ VPN panel type: {remnawave.vpn_panel_type}")
except Exception as e:
    log.warning(f"Remnawave config load warning: {e}")
    remnawave = _RemnawaveConfig()
