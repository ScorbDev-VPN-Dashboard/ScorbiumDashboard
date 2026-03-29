from decimal import Decimal
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field


class PlanCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    slug: str = Field(..., min_length=1, max_length=64, pattern=r"^[a-z0-9_]+$")
    duration_days: int = Field(..., gt=0)
    price: Decimal = Field(..., gt=0)
    description: Optional[str] = None
    currency: str = Field(default="RUB", max_length=8)
    sort_order: int = Field(default=0)


class PlanUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=128)
    description: Optional[str] = None
    duration_days: Optional[int] = Field(None, gt=0)
    price: Optional[Decimal] = Field(None, gt=0)
    currency: Optional[str] = Field(None, max_length=8)
    is_active: Optional[bool] = None
    sort_order: Optional[int] = None


class PlanRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    slug: str
    description: Optional[str]
    duration_days: int
    price: Decimal
    currency: str
    is_active: bool
    sort_order: int
