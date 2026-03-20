from typing import Dict, Optional
from pydantic import BaseModel
from typing_extensions import Any

from app.utils.log import log
from app.core.exceptions import *

class _Config(BaseModel):
    _initialized: bool = False
    _telegram_config: Optional[Any] = None
    _pasarguard_config: Optional[Any] = None
    _database_config: Optional[Any] = None
    _yookassa_config: Optional[Any] = None
    _utils_config: Optional[Any] = None
    
    model_config = {
        "arbitrary_types_allowed": True,
        "validate_assignment": True,
    }

    @property
    def telegram(self) -> Any:
        if self._telegram_config is None:
            from .configs import telegram
            self._telegram_config = telegram
        return self._telegram_config

    @property
    def pasarguard(self) -> Any:
        if self._pasarguard_config is None:
            from .configs import pasarguard
            self._pasarguard_config = pasarguard
        return self._pasarguard_config

    @property
    def database(self) -> Any:
        if self._database_config is None:
            from .configs import database
            self._database_config = database
        return self._database_config

    @property 
    def yookassa(self) -> Any:
        if self._yookassa_config is None:
            from .configs import yookassa
            self._yookassa_config = yookassa
        return self._yookassa_config
    
    @property 
    def utils(self) -> Any:
        if self._utils_config is None:
            from .configs import utils
            self._utils_config = utils
        return self._utils_config
    
    def initialize(self, force: bool = False, logger=None) -> Optional["_Config"]:
        if self._initialized and not force:
            return self
        
        log.info("Initializing all configs...")

        _ = self.telegram
        _ = self.pasarguard
        _ = self.database
        _ = self.yookassa
        _ = self.utils
        
        self._initialized = True
        
    def reload(self) -> "_Config":
        log.info("Reloading all configs...")
        
        self._db_settings = None
        self._pasarguard_settings = None
        self._telegram_settings = None
        self._utils_settings = None
        self._web_settings = None
        self._yookassa_settings = None
    
        assert not self._initialized is not None 
        return self._initialized(force=True)

    def validate_all(self) -> Dict[str, bool]:
        results = {}
        
        try:
            _ = self.database
            results["database"] = True
        except Exception as e:
            results["database"] = False
            log.error(f"Error validating database settings: {e}")
        
        try:
            _ = self.pasarguard
            results["pasarguard"] = True
        except Exception as e:
            results["pasarguard"] = False
            log.error(f"Error validating Pasarguard settings: {e}")
        
        try:
            _ = self.telegram
            results["telegram"] = True
        except Exception as e:
            results["telegram"] = False
            log.error(f"Error validating Telegram settings: {e}")
        
        try:
            _ = self.utils
            results["utils"] = True
        except Exception as e:
            results["utils"] = False
            log.error(f"Error validating utils settings: {e}")
        
        try:
            _ = self.yookassa
            results["yookassa"] = True
        except Exception as e:
            results["yookassa"] = False
            log.error(f"Error validating Yookassa settings: {e}")
        return results

    def __repr__(self):
        return f"<Config:(telegram: {self.telegram}, pasarguard: {self.pasarguard}, database: {self.database})>"

config = None        
try:
    config = _Config().initialize()
    log.success("✅ All configs initialized successfully")
except Exception as e:
    log.error(f"❌ Failed to initialize configs: {e}. \n Error in {__file__}: {e}")
