"""Bearer 鉴权（T009 + W2 任务 2 迁移）+ 管理面安全组件（W2 任务 1）。

网关面契约（W2 起消费 api_keys 表，替代 .env 固定 key）：`Authorization: Bearer <sk-key>`。
- 缺失 / scheme 非 Bearer → 401
- SHA-256 哈希查 api_keys.key_hash 无记录 → 401
- status != active（软删 revoked）→ 401；expires_at 已过 → 401
- 明文永不回读：返回 ApiKey 实体（哈希/前缀），端点经白名单校验后使用
- 比对用 constant-time（hmac.compare_digest），防时序侧信道

管理面（W2）：
- 密码哈希：标准库 hashlib.pbkdf2_hmac（随机盐，格式 `pbkdf2_sha256$iter$salt$hash`）
- JWT：pyjwt HS256 签发/校验（exp 过期、签名篡改 → 401）
"""

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from hmac import compare_digest
from typing import Any

import jwt
from fastapi import Header, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import AuthenticationError
from app.models import ApiKey
from app.services.keys import hash_sk_key

_PBKDF2_ITERATIONS = 100_000
_ACCESS_TOKEN_TTL = timedelta(hours=12)


def hash_password(password: str) -> str:
    """密码哈希：pbkdf2_hmac(sha256) + 随机盐。随机盐保证同密码两次哈希不同。"""
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt), _PBKDF2_ITERATIONS
    ).hex()
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    """校验密码：解析存储格式后重算比对（constant-time，防时序侧信道）。"""
    try:
        _, iterations, salt, expected = stored.split("$")
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt), int(iterations)
        ).hex()
    except ValueError:
        return False
    return hmac.compare_digest(digest, expected)


def create_access_token(
    sub: str | int,
    tenant_id: int,
    expires_delta: timedelta = _ACCESS_TOKEN_TTL,
) -> str:
    """签发管理面 JWT（HS256）：sub=用户 ID，tenant_id=租户隔离边界。"""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(sub),
        "tenant_id": tenant_id,
        "iat": now,
        "exp": now + expires_delta,
    }
    return jwt.encode(payload, get_settings().secret_key, algorithm="HS256")


def decode_access_token(token: str) -> dict[str, Any]:
    """校验管理面 JWT：签名篡改 / 已过期 → 401 authentication_error。"""
    try:
        return jwt.decode(
            token, get_settings().secret_key, algorithms=["HS256"]
        )
    except jwt.InvalidTokenError as exc:  # 含 ExpiredSignatureError / SignatureError
        raise AuthenticationError("invalid or expired token") from exc


def extract_bearer_token(authorization: str | None) -> str:
    """从 Authorization 头提取 Bearer token；缺失或 scheme 格式错误抛 401。"""
    if not authorization:
        raise AuthenticationError()
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise AuthenticationError()
    return token


async def require_gateway_api_key(
    request: Request,
    authorization: str | None = Header(default=None),
) -> ApiKey:
    """FastAPI 依赖（W2 任务 2）：网关面端点鉴权入口，返回 api_keys 表实体。

    顺序：头提取 → SHA-256 哈希查表 → status/expires_at 校验。
    校验通过返回 ApiKey（含 model_whitelist），端点据此做白名单授权。
    """
    token = extract_bearer_token(authorization)
    key_hash = hash_sk_key(token)
    async with AsyncSession(request.app.state.engine) as session:
        key = await session.scalar(
            select(ApiKey).where(ApiKey.key_hash == key_hash)
        )
    if key is None:
        raise AuthenticationError()
    if key.status != "active":
        raise AuthenticationError("key is inactive or revoked")
    if key.expires_at is not None and key.expires_at < datetime.now(timezone.utc):
        raise AuthenticationError("key has expired")
    return key


async def require_admin(
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """FastAPI 依赖：管理面端点的鉴权入口（Bearer JWT → claims）。

    返回解码后的 claims（sub=用户 ID、tenant_id=租户隔离边界），
    供管理面端点做租户维度的数据隔离与归属校验。
    """
    token = extract_bearer_token(authorization)
    return decode_access_token(token)