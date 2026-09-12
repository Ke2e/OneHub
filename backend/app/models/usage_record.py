"""用量记录表：每次调用一行，request_id 幂等唯一（W3 计费落库目标）。"""

from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class UsageRecord(Base):
    """单次调用用量：request_id 全局唯一（幂等防重复落库），按模型+tokens 计费。"""

    __tablename__ = "usage_records"
    __table_args__ = (
        # 计费对账/查询按 (api_key, 时间) 维度，DDL 指定此复合索引
        Index("idx_usage_key_time", "api_key_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    request_id: Mapped[UUID] = mapped_column(
        Uuid, unique=True, nullable=False
    )
    api_key_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("api_keys.id"), nullable=True
    )
    channel_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("channels.id"), nullable=True
    )
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_tokens: Mapped[Optional[int]] = mapped_column(Integer)
    completion_tokens: Mapped[Optional[int]] = mapped_column(Integer)
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer)
    status_code: Mapped[Optional[int]] = mapped_column(Integer)
    cost: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 6))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )