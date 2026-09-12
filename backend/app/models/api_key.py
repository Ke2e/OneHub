"""API Key 表：网关面调用凭证（W2 起消费，哈希存储）。"""

from datetime import datetime
from typing import List, Optional

from sqlalchemy import BigInteger, CHAR, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class ApiKey(Base):
    """调用方 Key：明文 `sk-` 前缀 + SHA-256 哈希（key_hash 唯一）。"""

    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id"), nullable=False
    )
    name: Mapped[Optional[str]] = mapped_column(String(64))
    key_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    key_hash: Mapped[str] = mapped_column(CHAR(64), unique=True, nullable=False)
    rpm_limit: Mapped[int] = mapped_column(Integer, server_default="60")
    tpm_limit: Mapped[int] = mapped_column(Integer, server_default="100000")
    quota_remaining: Mapped[int] = mapped_column(
        BigInteger, server_default="1000000"
    )
    model_whitelist: Mapped[Optional[List[str]]] = mapped_column(
        ARRAY(Text)
    )
    status: Mapped[str] = mapped_column(String(16), server_default="active")
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )