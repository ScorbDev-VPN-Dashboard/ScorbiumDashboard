import enum
from sqlalchemy import BigInteger, Column, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import relationship

from app.models.base import Base


class PaymentStatus(str, enum.Enum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"


class PaymentProvider(str, enum.Enum):
    # TODO: ADD PROVIDERS 
    # FREEKASSA = "freekassa"
    # AIKASSA = "ai_kassa"

    YOOKASSA = "yookassa"
    CRYPTOBOT = "cryptobot"
    TELEGRAM_STARS = "telegram_stars"
    BALANCE = "balance"

class Payment(Base):
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    vpn_key_id = Column(Integer, ForeignKey("vpn_keys.id", ondelete="SET NULL"), nullable=True)
    provider = Column(String(32), nullable=False)
    external_id = Column(String(256), nullable=True, unique=True)
    amount = Column(Numeric(10, 2), nullable=False)
    currency = Column(String(8), default="RUB", nullable=False)
    status = Column(String(16), default=PaymentStatus.PENDING.value, nullable=False)
    meta = Column(Text, nullable=True)  # JSON строка с доп. данными

    user = relationship("User", back_populates="payments")

    def __repr__(self) -> str:
        return f"<Payment id={self.id} provider={self.provider} status={self.status} amount={self.amount}>"
