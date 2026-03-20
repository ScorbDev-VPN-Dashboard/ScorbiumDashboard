from typing import Literal, Optional
import re
from pathlib import Path
from pydantic import Field, model_validator, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.utils.log import log
from app.utils.path import env_file
from app.core.exceptions import *

class _UtilsConfig(BaseSettings):
    """
    Configuration for utility settings such as logging
    Parameters:
    - LOG_PATH: Path to store log files (default: "app/logs")
    - LOG_ROTATION: Log rotation policy (default: "1 day")
    - LOG_RETENTION: Log retention policy (default: "30 days")
    - LOG_LEVEL: Log level (default: "INFO")
    """
    model_config = SettingsConfigDict(
        env_file=env_file,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        frozen=True,
    )
    
    log_path: Optional[Path] = Field(default=Path("app/logs"), validation_alias="LOG_PATH")
    log_rotation: Optional[str] = Field(default="1 day", validation_alias="LOG_ROTATION")
    log_retention: Optional[str] = Field(default="30 days", validation_alias="LOG_RETENTION")
    log_level: Optional[str] = Field(default="INFO", validation_alias="LOG_LEVEL")
    
    @field_validator('log_path', mode='before')
    def validate_log_path(cls, value):
        if value is None:
            return Path("logs")
        path = Path(value)
        try:
            path.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            raise EnvException(f"Cannot create folder for logs {path}: {e}")
        return path
    
    @field_validator('log_rotation')
    def validate_rotation(cls, value):
        if value is None:
            return "1 day"
        valid = re.match(r'^\d+\s*(day|days|hour|hours|MB|GB)?$', str(value))
        if not valid:
            raise EnvException(f"Invalid format for rotation: {value}. Example: 1 day, 100 MB")
        return str(value)
    
    @field_validator('log_retention')
    def validate_retention(value):
        if value is None:
            return "30 days"
        valid = re.match(r'^\d+\s*(day|days|month|months)?$', str(value))
        if not valid:
            raise EnvException(f"Invalid format for retention: {value}. Example: 30 days")
        return str(value)
    
    @field_validator('log_level')
    def validate_level(cls, v):
        valid_levels = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
        if v.upper() not in valid_levels:
            raise EnvException(f"Level logs can be one of: {valid_levels}")
        return v.upper()
    
    @model_validator(mode='after')
    def check_log_settings(self):
        if self.log_rotation and self.log_retention:
            pass
        return self

utils = None
try:
    utils = _UtilsConfig()
    log.success("✅ Utils config initialized successfully")
    log.debug(f"Utils: {utils}")

except Exception as e:
    log.error(f"❌ Failed to initialize Utils config: {e}")