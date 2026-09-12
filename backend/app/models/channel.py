"""渠道表：上游 LLM 提供方实例（W1 消费：seed 写入 deepseek-main）。"""

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class Channel(Base):
    """上游渠道：名称/提供方/base_url + 密钥占位（W1 明文占位，W4 加密走 ADR）。"""

    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    base_url: Mapped[str] = mapped_column(String(255), nullable=False)
    api_key_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    weight: Mapped[int] = mapped_column(Integer, server_default="10")
    status: Mapped[str] = mapped_column(String(16), server_default="healthy")
    failure_count: Mapped[int] = mapped_column(Integer, server_default="0")
    opened_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )