"""管理面请求/响应模型（W2 任务 1：auth + keys CRUD；W3 任务 1：models 定价 CRUD）。"""

from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field


class RegisterRequest(BaseModel):
    """租户管理员注册：建 Tenant + User，签发 JWT。"""

    email: str = Field(min_length=3, max_length=128)
    password: str = Field(min_length=8, max_length=128)
    tenant_name: str = Field(default="default", max_length=64)


class LoginRequest(BaseModel):
    """管理面登录：email + 密码 → JWT。"""

    email: str
    password: str


class CreateKeyRequest(BaseModel):
    """创建网关面 SK-Key：明文只返回一次，DB 仅存 SHA-256 哈希。"""

    name: Optional[str] = Field(default=None, max_length=64)
    model_whitelist: Optional[list[str]] = None
    rpm_limit: int = Field(default=60, ge=1)
    tpm_limit: int = Field(default=100000, ge=1)
    expires_at: Optional[datetime] = None


class ModelCreate(BaseModel):
    """创建模型定价记录（W3 任务 1）：全局模型清单 + 进/出价 + 启停。"""

    model_name: str = Field(min_length=1, max_length=64)
    channel_id: Optional[int] = None
    input_price: Optional[Decimal] = None
    output_price: Optional[Decimal] = None
    enabled: bool = True


class ModelUpdate(BaseModel):
    """局部更新模型（PATCH）：仅覆盖提供的字段。"""

    model_name: Optional[str] = Field(default=None, min_length=1, max_length=64)
    channel_id: Optional[int] = None
    input_price: Optional[Decimal] = None
    output_price: Optional[Decimal] = None
    enabled: Optional[bool] = None


class ChannelCreate(BaseModel):
    """创建渠道（W4 任务 2）：名称/提供方/base_url/权重/状态。密钥占位或透传。"""

    name: str = Field(min_length=1, max_length=64)
    provider: str = Field(default="deepseek", max_length=32)
    base_url: str = Field(min_length=1, max_length=255)
    api_key_encrypted: Optional[str] = None  # 不传则存占位（密钥加密留 W4 收尾统一加固）
    weight: int = Field(default=10, ge=1)
    status: str = Field(default="healthy", max_length=16)


class ChannelUpdate(BaseModel):
    """局部更新渠道（PATCH）：仅覆盖提供的字段。"""

    name: Optional[str] = Field(default=None, min_length=1, max_length=64)
    provider: Optional[str] = Field(default=None, max_length=32)
    base_url: Optional[str] = Field(default=None, min_length=1, max_length=255)
    api_key_encrypted: Optional[str] = None
    weight: Optional[int] = Field(default=None, ge=1)
    status: Optional[str] = Field(default=None, max_length=16)