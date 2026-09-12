"""T009 鉴权头测试（W2 任务 2 迁移后精简）：Bearer 提取与格式校验。

W2 起网关面鉴权主体迁到 api_keys 表（见 test_gateway_auth.py 全分支），
本文件保留无状态头解析部分：缺失 / scheme 非 Bearer / 无 token → 401。
"""

import pytest

from app.core.errors import AuthenticationError
from app.core.security import extract_bearer_token


def test_missing_authorization_header_raises():
    """分支 1 缺失：无 Authorization 头 → 401。"""
    with pytest.raises(AuthenticationError):
        extract_bearer_token(None)
    with pytest.raises(AuthenticationError):
        extract_bearer_token("")


def test_non_bearer_scheme_raises():
    """分支 2 格式错误：scheme 不是 Bearer（Basic/Token）→ 401。"""
    with pytest.raises(AuthenticationError):
        extract_bearer_token("Basic abc-123")
    with pytest.raises(AuthenticationError):
        extract_bearer_token("Token abc-123")


def test_bearer_scheme_without_token_raises():
    """分支 3 格式错误补：Bearer 后无 token → 401。"""
    with pytest.raises(AuthenticationError):
        extract_bearer_token("Bearer")


def test_extract_bearer_returns_token():
    """提取函数：合法头 → 返回 token。"""
    assert extract_bearer_token("Bearer abc-123") == "abc-123"
    # scheme 大小写不敏感（RFC 7235）
    assert extract_bearer_token("bearer abc-123") == "abc-123"