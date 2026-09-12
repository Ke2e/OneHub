"""T022 US4 端点单测：GET /v1/models（离线桩 AsyncSession，不依赖 Docker pg）。

设计决策（2026-09-12 新窗口定）：T022 的 DB 来源取"桩"而非真实测试库——
与现有单测"mock 外部依赖"风格一致（T014 用 MockTransport mock 网络），
最简且离线可复跑。桩模拟 DB 层 `WHERE enabled` 过滤语义，端点逻辑完整覆盖。

W2 任务 2 迁移后：网关面鉴权走 api_keys 表（哈希校验），桩预置
ApiKey(key_hash=SHA256("test-gateway-key"))，security 与 gateway 两模块的
AsyncSession 指向同一桩（依赖与端点查询同库）。

数据用种子形态（Phase 3 后 id 为 SenseAudio 实际可用模型）：
- 2 个 enabled：deepseek-v4-flash-0731 / senseaudio-s2
- 1 个 disabled：验证"未启用不出现"（US4 验收语义）

created/owned_by 为协议壳层静态默认值（T021 定：0 / "onehub"，contracts
GET /v1/models 节对齐）。鉴权（contracts「全部网关面端点」）：无 key → 401。
"""

import pytest
from fastapi.testclient import TestClient

import app.api.v1.gateway as gateway_module
import app.core.security as security_module
from app.main import create_app
from app.models import ApiKey, Model
from app.services.keys import hash_sk_key
from tests._fake_db import FakeSession

TEST_KEY = "test-gateway-key"


@pytest.fixture
def client(monkeypatch):
    """预置 ApiKey + 3 模型，patch 鉴权与端点两处 AsyncSession 指向同一桩。"""
    factory = lambda *a, **k: factory.shared
    factory.shared = FakeSession(
        [
            ApiKey(
                tenant_id=1,
                name="test",
                key_prefix=TEST_KEY[:10],
                key_hash=hash_sk_key(TEST_KEY),
                status="active",
                expires_at=None,
                model_whitelist=None,
            ),
            Model(model_name="deepseek-v4-flash-0731", enabled=True),
            Model(model_name="senseaudio-s2", enabled=True),
            Model(model_name="disabled-model", enabled=False),
        ]
    )
    monkeypatch.setattr(security_module, "AsyncSession", factory)
    monkeypatch.setattr(gateway_module, "AsyncSession", factory)
    with TestClient(create_app(), raise_server_exceptions=False) as c:
        yield c


def test_models_list_shape_and_content(client):
    """种子数据 → OpenAI list 结构 + 只含 enabled 模型（空 disabled 不出现）。"""
    resp = client.get(
        "/v1/models", headers={"Authorization": f"Bearer {TEST_KEY}"}
    )
    assert resp.status_code == 200
    body = resp.json()

    # 协议壳层：object=list，data[] 每项 {id, object: "model", created, owned_by}
    assert body["object"] == "list"
    assert [m["id"] for m in body["data"]] == [
        "deepseek-v4-flash-0731",
        "senseaudio-s2",
    ]
    assert all(m["object"] == "model" for m in body["data"])
    assert all(m["created"] == 0 for m in body["data"])
    assert all(m["owned_by"] == "onehub" for m in body["data"])


def test_models_requires_api_key(client):
    """鉴权（contracts 全端点多面）：无 Bearer key → 401 authentication_error。"""
    resp = client.get("/v1/models")
    assert resp.status_code == 401
    body = resp.json()
    assert body["error"]["type"] == "authentication_error"