"""Bearer 鉴权（T009）：网关面固定 Key 校验。

契约：`Authorization: Bearer <GATEWAY_API_KEY>`。
- 缺失 / scheme 非 Bearer / 值不符 → 一律 401 authentication_error
- 比对用 constant-time（hmac.compare_digest），防时序侧信道
"""

from hmac import compare_digest

from fastapi import Header

from app.core.config import get_settings
from app.core.errors import AuthenticationError


def extract_bearer_token(authorization: str | None) -> str:
    """从 Authorization 头提取 Bearer token；缺失或 scheme 格式错误抛 401。"""
    if not authorization:
        raise AuthenticationError()
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise AuthenticationError()
    return token


def verify_gateway_api_key(authorization: str | None) -> str:
    """校验 key：constant-time 比对配置值，不匹配抛 401。"""
    token = extract_bearer_token(authorization)
    expected = get_settings().gateway_api_key
    if not compare_digest(token.encode(), expected.encode()):
        raise AuthenticationError()
    return token


async def require_gateway_api_key(
    authorization: str | None = Header(default=None),
) -> str:
    """FastAPI 依赖：网关面端点的鉴权入口。"""
    return verify_gateway_api_key(authorization)