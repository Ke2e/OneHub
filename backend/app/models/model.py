"""模型表：网关可用的模型清单（定价 + 启停 + 渠道归属）。"""

from decimal import Decimal
from typing import Optional

from sqlalchemy import Boolean, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class Model(Base):
    """模型：model_name 全局名（如 deepseek-chat），挂载到渠道，可启停。"""

    __tablename__ = "models"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    model_name: Mapped[str] = mapped_column(String(64), nullable=False)
    channel_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("channels.id"), nullable=True
    )
    input_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 6))
    output_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 6))
    enabled: Mapped[bool] = mapped_column(Boolean, server_default="true")