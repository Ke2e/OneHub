"""T009 鉴权单测：Bearer Key 校验四分支（有效/缺失/值错误/格式错误）。"""

import pytest

from app.core.errors import AuthenticationError
from app.core.security import extract_bearer_token, verify_gateway_api_key

VALID_KEY = "test-gateway-key"  # conftest 注入的 GATEWAY_API_KEY


def test_valid_bearer_token_passes():
    """分支 1 有效：Bearer + 正确 key → 校验通过并回显 token。"""
    token = verify_gateway_api_key(f"Bearer {VALID_KEY}")
    assert token == VALID_KEY


def test_missing_authorization_header_raises():
    """分支 2 缺失：无 Authorization 头 → 401。"""
    with pytest.raises(AuthenticationError):
        verify_gateway_api_key(None)


def test_wrong_key_value_raises():
    """分支 3 值错误：Bearer + 错误 key → 401。"""
    with pytest.raises(AuthenticationError):
        verify_gateway_api_key(f"Bearer {VALID_KEY}-wrong")


def test_non_bearer_scheme_raises():
    """分支 4 格式错误：scheme 不是 Bearer（Basic/Token）→ 401。"""
    with pytest.raises(AuthenticationError):
        verify_gateway_api_key(f"Basic {VALID_KEY}")
    with pytest.raises(AuthenticationError):
        verify_gateway_api_key(f"Token {VALID_KEY}")


def test_bearer_scheme_without_token_raises():
    """分支 4 格式错误补：Bearer 后无 token → 401。"""
    with pytest.raises(AuthenticationError):
        verify_gateway_api_key("Bearer")


def test_extract_bearer_returns_token():
    """提取函数：合法头 → 返回 token；异常头 → 401。"""
    assert extract_bearer_token("Bearer abc-123") == "abc-123"
    with pytest.raises(AuthenticationError):
        extract_bearer_token("")
    with pytest.raises(AuthenticationError):
        extract_bearer_token("Bearer")
    with pytest.raises(AuthenticationError):
        extract_bearer_token("Basic abc-123")