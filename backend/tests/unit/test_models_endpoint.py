"""T022 US4 端点单测：GET /v1/models（离线桩 AsyncSession，不依赖 Docker pg）。

设计决策（2026-09-12 新窗口定）：T022 的 DB 来源取"桩"而非真实测试库——
与现有单测"mock 外部依赖"风格一致（T014 用 MockTransport mock 网络），
最简且离线可复跑。桩模拟 DB 层 `WHERE enabled` 过滤语义，端点逻辑完整覆盖。

数据用种子形态（Phase 3 后 id 为 SenseAudio 实际可用模型）：
- 2 个 enabled：deepseek-v4-flash-0731 / senseaudio-s2
- 1 个 disabled：验证"未启用不出现"（US4 验收语义）

created/owned_by 为协议壳层静态默认值（T021 定：0 / "onehub"，contracts
GET /v1/models 节对齐）。鉴权（contracts「全部网关面端点」）：无 key → 401。
"""

import pytest
from fastapi.testclient import TestClient

from app.api.v1 import gateway as gateway_module
from app.main import create_app
from app.models import Model


class _FakeScalars:
    """模拟 AsyncScalarResult：仅支持 .all()。"""

    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeSession:
    """离线桩：async with 上下文 + scalars() 模拟 DB 层 enabled 过滤。

    真实端点构造 `AsyncSession(request.app.state.engine)` 并执行
    `select(Model).where(Model.enabled.is_(True))`——桩在 db 层过滤后返回，
    让"未启用不出现"语义可在单测断言（过滤本身属 where 语义，真机验收兜底）。
    """

    def __init__(self, rows):
        self._rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def scalars(self, stmt):
        enabled = [m for m in self._rows if m.enabled is True]
        return _FakeScalars(enabled)


@pytest.fixture
def client(monkeypatch):
    """桩化 gateway.AsyncSession 后挂真实 app（lifespan 惰性建连，离线安全）。"""
    rows = [
        Model(model_name="deepseek-v4-flash-0731", enabled=True),
        Model(model_name="senseaudio-s2", enabled=True),
        Model(model_name="disabled-model", enabled=False),
    ]
    session = _FakeSession(rows)

    def _fake_session_factory(*args, **kwargs):
        return session

    monkeypatch.setattr(gateway_module, "AsyncSession", _fake_session_factory)
    with TestClient(create_app(), raise_server_exceptions=False) as c:
        yield c


def test_models_list_shape_and_content(client):
    """种子数据 → OpenAI list 结构 + 只含 enabled 模型（空 disabled 不出现）。"""
    resp = client.get(
        "/v1/models", headers={"Authorization": "Bearer test-gateway-key"}
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