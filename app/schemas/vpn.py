from datetime import datetime
from decimal import Decimal
from pydantic import BaseModel, ConfigDict
from typing import Optional
from app.models.vpn_key import VpnKeyStatus


class VpnKeyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    plan_id: Optional[int] = None
    pasarguard_key_id: Optional[str] = None
    access_url: str
    name: Optional[str] = None
    price: Optional[Decimal] = None
    expires_at: Optional[datetime] = None
    status: VpnKeyStatus


class VpnKeyCreate(BaseModel):
    user_id: int
    access_url: str
    name: Optional[str] = None
