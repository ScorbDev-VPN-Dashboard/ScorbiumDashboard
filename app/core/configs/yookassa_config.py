from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache
from typing import Optional
import re

from app.utils.path import env_file
from app.core.exceptions import *
from app.utils.log import log

class _YookassaConfig(BaseSettings):
    """
    Configuration for Yookassa payment system
    Parameters:
    - YOOKASSA_SHOP_ID: Yookassa shop ID
    - YOOKASSA_SECRET_KEY: Yookassa secret key
    """

    model_config = SettingsConfigDict(
        env_file=env_file,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
        frozen=True,
    )
    
    yookassa_shop_id: Optional[int] = Field(
        default=None,
        ge=10000,
        le=999999999,
        validation_alias="YOOKASSA_SHOP_ID"
    )
    
    yookassa_secret_key: Optional[SecretStr] = Field(
        default=None,
        validation_alias="YOOKASSA_SECRET_KEY"
    )
    
    @field_validator("yookassa_shop_id")
    @classmethod
    def validate_yookassa_shop_id(cls, value: Optional[int]) -> Optional[int]:
        if value is not None:
            shop_id_str = str(value)
            if not (5 <= len(shop_id_str) <= 8):
                raise YookassaValueError(f"Shop ID должен содержать от 5 до 8 цифр, получено: {len(shop_id_str)}")
            log.debug(f"✅ Yookassa Shop ID валидация пройдена: {value}")
        return value
        
    @field_validator("yookassa_secret_key")
    @classmethod
    def validate_yookassa_secret_key(cls, value: Optional[SecretStr]) -> Optional[SecretStr]:
        if value is not None:
            secret_value = value.get_secret_value()
            if not secret_value or len(secret_value) < 10:
                raise YookassaValueError("Секретный ключ слишком короткий или пустой")
            
            if not re.match(r'^[A-Za-z0-9_\-]+$', secret_value):
                raise YookassaValueError("Секретный ключ содержит недопустимые символы")
                
            log.debug("✅ Yookassa Secret Key валидация пройдена")
        return value
    
    @property
    def get_auth(self) -> Optional[dict]:
        if self.yookassa_shop_id and self.yookassa_secret_key:
            log.info("✅ Successful using Yookassa Payment")
            
            return {
                "shop_id": self.yookassa_shop_id,
                "secret_key": self.yookassa_secret_key.get_secret_value()
            }
            
        log.warning("⚠️ Not using Yookassa Payment, check '.env'")
        return None

@lru_cache()
def get_yookassa_config() -> _YookassaConfig:
    return _YookassaConfig()
  
try:
    yookassa = get_yookassa_config()
    log.success("✅ Yookassa config initialized successfully")
    log.debug(f"Yookassa: {yookassa}")
except Exception as e:
    log.error(f"❌ Failed to initialize Yookassa config: {e}. \n Error in {__file__}: {e}")
