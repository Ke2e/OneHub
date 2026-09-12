"""管理面 auth（W2 任务 1）：注册/登录 → JWT 签发。

- register：建 Tenant + User（密码 pbkdf2 哈希），重复 email → 409 conflict_error
- login：验密码 → 200 + token；错密码/未知 email → 401 authentication_error（不泄露账号存在性）
"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AuthenticationError, ConflictError
from app.core.security import create_access_token, hash_password, verify_password
from app.models import Tenant, User
from app.schemas.admin import LoginRequest, RegisterRequest

router = APIRouter(prefix="/api/auth", tags=["admin"])


async def _issue_token(user: User, tenant: Tenant) -> str:
    """签发 JWT：sub=用户 ID，tenant_id=租户隔离边界。"""
    return create_access_token(sub=user.id, tenant_id=tenant.id)


@router.post("/register", status_code=201)
async def register(req: RegisterRequest, request: Request) -> dict:
    """创建租户 + 管理员用户，签发管理面 JWT。"""
    async with AsyncSession(request.app.state.engine) as session:
        existing = await session.scalar(
            select(User).where(User.email == req.email)
        )
        if existing:
            raise ConflictError("email already registered")

        tenant = Tenant(name=req.tenant_name)
        session.add(tenant)
        await session.flush()  # 回填 tenant.id
        user = User(
            tenant_id=tenant.id,
            email=req.email,
            password_hash=hash_password(req.password),
            role="admin",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        await session.refresh(tenant)

    return {
        "token": await _issue_token(user, tenant),
        "user": {"id": user.id, "email": user.email, "role": user.role},
        "tenant": {"id": tenant.id, "name": tenant.name},
    }


@router.post("/login")
async def login(req: LoginRequest, request: Request) -> dict:
    """校验邮箱+密码，签发管理面 JWT。"""
    async with AsyncSession(request.app.state.engine) as session:
        user = await session.scalar(
            select(User).where(User.email == req.email)
        )
    if user is None or not verify_password(req.password, user.password_hash):
        # 统一 401：不区分「用户不存在」与「密码错误」，避免账号枚举
        raise AuthenticationError("invalid email or password")

    async with AsyncSession(request.app.state.engine) as session:
        tenant = await session.get(Tenant, user.tenant_id)

    return {
        "token": await _issue_token(user, tenant),
        "user": {"id": user.id, "email": user.email, "role": user.role},
        "tenant": {"id": tenant.id, "name": tenant.name},
    }