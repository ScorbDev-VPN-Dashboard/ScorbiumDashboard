from typing import Any, Dict, Optional, Tuple, Union, cast

from app.utils.log import log
from pydantic import Field, HttpUrl, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.utils.path import env_file

from app.core.exceptions import *


class _PasarGuardConfig(BaseSettings):
    """
    Configuration for Pasarguard API
    Parameters:
    - PASARGUARD_ADMIN_PANEL: URL of the Pasarguard admin panel (required)
    - PASARGUARD_ADMIN_LOGIN: Admin login for authentication (optional if API key is provided)
    - PASARGUARD_ADMIN_PASSWORD: Admin password for authentication (optional if API key is provided)
    - PASARGUARD_API_KEY: API key for authentication (optional if login/password is provided)
    At least one authentication method must be provided: either login/password or API key.
    """

    model_config = SettingsConfigDict(
        env_file=env_file,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        frozen=True,
    )

    pasarguard_admin_panel: HttpUrl = Field(
        default=...,
        validation_alias="PASARGUARD_ADMIN_PANEL",
        description="URL Admin panel Pasarguard",
    )

    pasarguard_admin_login: Optional[str] = Field(
        default=None,
        validation_alias="PASARGUARD_ADMIN_LOGIN",
        description="login (for auth on login/password)",
    )

    pasarguard_admin_password: Optional[SecretStr] = Field(
        default=None,
        validation_alias="PASARGUARD_ADMIN_PASSWORD",
        description="Admin Password (for auth on login/password)",
    )

    pasarguard_api_key: Optional[SecretStr] = Field(
        default=None,
        validation_alias="PASARGUARD_API_KEY",
        description="API Key for auth",
    )

    @field_validator("pasarguard_admin_panel")
    @classmethod
    def validate_admin_panel_url(cls, value: HttpUrl) -> HttpUrl:
        """Validate URL Admin panel"""

        if value.host in ["localhost", "127.0.0.1", "0.0.0.0"]:
            log.warning(f"⚠️ Using localhost for admin panel: {value}")

        if value.scheme == "http" and value.host not in ["localhost", "127.0.0.1"]:
            log.warning(f"⚠️ Admin panel URL uses HTTP (not secure): {value}")

        if not value.path or value.path == "/":
            log.warning(f"⚠️ Admin panel URL has no specific path: {value}")

        return value

    @model_validator(mode="after")
    def validate_authentication_method(self) -> "_PasarGuardConfig":
        has_password_auth = (
            self.pasarguard_admin_login is not None
            and self.pasarguard_admin_password is not None
        )

        has_api_key = self.pasarguard_api_key is not None

        if not (has_password_auth or has_api_key):
            raise PasarguardAuthError(
                "At least one authentication method must be specified:\n"
                "- Login and password (PASARGUARD_ADMIN_LOGIN + PASARGUARD_ADMIN_PASSWORD)\n"
                "- API key (PASARGUARD_API_KEY)"
            )

        if has_password_auth and has_api_key:
            log.info("🔐 Using both: login/password and API Key")
        elif has_password_auth:
            log.info("🔐 Using: login/password")
        elif has_api_key:
            log.info("🔐 Using: API Key")

        return self

    @field_validator("pasarguard_admin_login")
    @classmethod
    def validate_username(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and len(value.strip()) == 0:
            raise EnvException("⚠ 'pasarguard_admin_login' cannot be empty!")
        return value

    def get_auth_headers(self) -> Dict[str, str]:
        """Get authentication headers for API requests"""
        headers = {}

        if self.pasarguard_api_key:
            headers["X-API-Key"] = self.pasarguard_api_key.get_secret_value()
            log.debug("Using API key authentication for headers")
        elif self.has_password_auth:
            log.debug("Password authentication will be used for token acquisition")

        return headers

    def get_auth_data(self) -> Dict[str, str]:
        """Get authentication data for login requests"""
        auth_data = {}

        if self.pasarguard_admin_login and self.pasarguard_admin_password:
            auth_data = {
                "username": self.pasarguard_admin_login,
                "password": self.pasarguard_admin_password.get_secret_value(),
            }
            log.debug("Using password authentication data")

        return auth_data

    @property
    def has_password_auth(self) -> bool:
        """Check if password authentication is available"""
        return bool(self.pasarguard_admin_login and self.pasarguard_admin_password)

    @property
    def has_api_key(self) -> bool:
        """Check if API key authentication is available"""
        return bool(self.pasarguard_api_key)

    @property
    def assert_api_key(self) -> Optional[SecretStr]:
        if self.pasarguard_admin_password is not None:
            return cast(SecretStr, self.pasarguard_admin_password)
        return None

    @property
    def assert_login_credentials(self) -> Optional[Tuple[str, SecretStr]]:
        if self.pasarguard_admin_login and self.pasarguard_admin_password is not None:
            return (
                cast(str, self.pasarguard_admin_login),
                cast(SecretStr, self.pasarguard_admin_password),
            )
        return None

    @property
    def get_data_pasarguard(self) -> Union[SecretStr, Tuple[str, SecretStr], None]:
        """
        Get authentication data for Pasarguard based on available method

        Returns:
            - SecretStr: if API key auth is available
            - Tuple[str, SecretStr]: (login, password) if password auth is available
            - None: if no auth method is available
        """
        if not self.has_api_key and not self.has_password_auth:
            raise EnvException(
                "❌ Cannot get Pasarguard Auth method❗️Check '.env' file ⚠️"
            )

        if self.has_api_key:
            log.info("ℹ️ For Pasarguard Auth using API Key")
            return cast(SecretStr, self.pasarguard_api_key)

        if self.has_password_auth:
            log.info("ℹ️ For Pasarguard Auth using login/password")
            return (
                cast(str, self.pasarguard_admin_login),
                cast(SecretStr, self.pasarguard_admin_password),
            )

        return None

    def get_auth_method_info(self) -> Dict[str, Any]:
        """
        Get information about authentication method

        Returns:
            Dictionary with auth method type and data
        """
        if self.has_api_key:
            return {
                "method": "api_key",
                "api_key": self.pasarguard_api_key,
                "has_password": False,
            }
        elif self.has_password_auth:
            return {
                "method": "password",
                "login": self.pasarguard_admin_login,
                "password": self.pasarguard_admin_password,
                "has_api_key": False,
            }
        else:
            return {"method": None, "has_api_key": False, "has_password": False}

    def get_api_client_config(self) -> Dict[str, Any]:
        """
        Get complete configuration for API client

        Returns:
            Dictionary with base_url and authentication configuration
        """
        config = {
            "base_url": str(self.pasarguard_admin_panel),
            "auth_method": "api_key"
            if self.has_api_key
            else "password"
            if self.has_password_auth
            else None,
        }

        if self.has_api_key:
            config["api_key"] = self.pasarguard_api_key
        elif self.has_password_auth:
            config["login"] = self.pasarguard_admin_login
            config["password"] = self.pasarguard_admin_password

        return config

    def __str__(self) -> str:
        auth_methods = []
        if self.has_password_auth:
            auth_methods.append("🔑 Password")
        if self.has_api_key:
            auth_methods.append("🔐 API Key")

        return (
            f"PasarGuardConfig(\n"
            f"  URL: {self.pasarguard_admin_panel}\n"
            f"  Auth: {', '.join(auth_methods) if auth_methods else '❌ None'}\n"
            f")"
        )

pasarguard = None
try:
    pasarguard = _PasarGuardConfig()
    log.success("✅ Pasarguard config initialized successfully")
    log.debug(f"Pasarguard: {pasarguard}")
except EnvException as e:
    log.error(f"❌ Failed to initialize Pasarguard config: {e}")
    log.error(
        "Check .env file. The following must be specified:\n"
        "  PASARGUARD_ADMIN_PANEL=https://your-panel.com\n"
        "  And either:\n"
        "    - PASARGUARD_ADMIN_LOGIN + PASARGUARD_ADMIN_PASSWORD\n"
        "    - PASARGUARD_API_KEY"
    )
    raise


__all__ = ["pasarguard"]
 