"""SK-Key 生成 / 哈希（W2 任务 1）：管理面发放的网关面调用凭证。

明文只存在创建瞬间，入库仅 key_hash（SHA-256 hex，对齐 api_keys.key_hash CHAR(64)）
与 key_prefix（UI 识别用）。密钥永不落盘、永不打印。
"""

import hashlib
import secrets

SK_KEY_PREFIX = "sk-"


def hash_sk_key(plaintext: str) -> str:
    """SHA-256 hex（64 位）：仅存储哈希，不存明文。"""
    return hashlib.sha256(plaintext.encode()).hexdigest()


def generate_sk_key() -> tuple[str, str, str]:
    """生成 (明文, key_prefix, key_hash)。

    - 明文：`sk-` + 32 hex（secrets.token_hex(16)≈128 bit 熵）
    - key_prefix：明文头部（sk- + 前 8 hex），列表/审计展示用，不可反推明文
    """
    plaintext = SK_KEY_PREFIX + secrets.token_hex(16)
    key_prefix = plaintext[: 10]
    return plaintext, key_prefix, hash_sk_key(plaintext)