"""SQLAlchemy 模型包：声明基类 + 7 表聚合导出（T005）。

Base 在此定义并先于子模块导入完成绑定，
各子模块经 `from app.models import Base` 继承，避免循环导入。
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """全表共享声明基类。"""


# 聚合导出（顺序满足外键引用在 metadata 中先行注册，顺序本身无强约束）
from .api_key import ApiKey
from .balance import Balance
from .channel import Channel
from .model import Model
from .tenant import Tenant
from .usage_record import UsageRecord
from .user import User

__all__ = [
    "Base",
    "Tenant",
    "User",
    "ApiKey",
    "Channel",
    "Model",
    "UsageRecord",
    "Balance",
]