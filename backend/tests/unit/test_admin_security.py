"""W2 任务 1 RED：管理面安全组件测试先行（测试先于实现，git 时间序自证）。

范围（Asize 批准的 auth + keys CRUD 最小闭环）：
- 密码哈希：pbkdf2_hmac（标准库，随机盐），verify 支持"盐不同哈希不同"
- SK-Key：`sk-` 前缀 + 32 hex 密文，SHA-256 hex（64 位 = api_keys.key_hash CHAR(64)）
  入库仅存 key_hash + key_prefix，明文只在创建瞬间交还调用方
- JWT：HS256 签发/校验（exp 过期、篡改 → 401 authentication_error）
"""

from datetime import timedelta

import pytest

from app.core.errors import AuthenticationError
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.services.keys import generate_sk_key, hash_sk_key


# ── 密码哈希 ─────────────────────────────────────────────

def test_password_hash_roundtrip():
    """正确密码可验证；错误密码不通过；存库值不含明文。"""
    stored = hash_password("s3cret-pass")
    assert stored != "s3cret-pass"
    assert verify_password("s3cret-pass", stored)
    assert not verify_password("wrong-pass", stored)


def test_password_hash_salted():
    """同密码两次哈希不同（随机盐），且都能通过验证。"""
    h1 = hash_password("same-password")
    h2 = hash_password("same-password")
    assert h1 != h2
    assert verify_password("same-password", h1)
    assert verify_password("same-password", h2)


# ── JWT 签发 / 校验 ─────────────────────────────────────

def test_access_token_roundtrip():
    """签发 → 解码回退 sub（用户）与 tenant_id 声明。"""
    token = create_access_token(sub="1", tenant_id=7)
    claims = decode_access_token(token)
    assert claims["sub"] == "1"
    assert claims["tenant_id"] == 7


def test_access_token_expired_raises():
    """过期 token → 401 authentication_error。"""
    token = create_access_token(
        sub="1", tenant_id=7, expires_delta=timedelta(seconds=-10)
    )
    with pytest.raises(AuthenticationError):
        decode_access_token(token)


def test_access_token_tampered_raises():
    """篡改签名/载荷 → 401 authentication_error。"""
    token = create_access_token(sub="1", tenant_id=7)
    if token[-1] == "a":
        tampered = token[:-1] + "b"
    else:
        tampered = token[:-1] + "a"
    with pytest.raises(AuthenticationError):
        decode_access_token(tampered)


# ── SK-Key 生成 / 哈希（哈希入库，明文仅创建时返回） ────

def test_generate_sk_key_shape():
    """明文带 sk- 前缀；哈希 64 位 hex（对齐 CHAR(64)）；prefix 是明文头部。"""
    plain, prefix, khash = generate_sk_key()
    assert plain.startswith("sk-")
    assert len(khash) == 64
    assert hash_sk_key(plain) == khash
    assert plain.startswith(prefix)
    assert prefix != plain


def test_generate_sk_key_unique_and_opaque():
    """100 次生成全唯一；哈希不可反推明文（无碰撞 + 非明文本身）。"""
    generated = {generate_sk_key() for _ in range(100)}
    assert len(generated) == 100
    plain, prefix, khash = next(iter(generated))
    assert khash != plain