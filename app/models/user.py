from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy import Column, DateTime, func
from typing import Any

Base = declarative_base()


class BaseModel(Base):
    __tablename__ = "base_model"
    
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), nullable=True)
    
    def dict(self) -> dict[str, Any]:
        """Преобразует модель в словарь"""
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}
    
    def update(self, **kwargs) -> None:
        """Обновляет поля модели"""
        for key, value in kwargs.items():
            if hasattr(self, key) and value is not None:
                setattr(self, key, value)