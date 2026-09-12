"""管理面请求/响应模型（W2 任务 1：auth + keys CRUD）。"""

from datetime import datetime
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