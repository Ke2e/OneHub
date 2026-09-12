"""余额表：按租户账面余额，version 乐观锁（W3 并发扣减）。"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, ForeignKey, Numeric, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class Balance(Base):
    """租户余额：tenant_id 主键 + version 乐观锁字段（W3 扣减 CAS）。"""

    __tablename__ = "balances"

    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id"), primary_key=True
    )
    balance: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )