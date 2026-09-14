"""W3 任务 1 RED：管理面 /api/models 定价 CRUD 测试先行。

范围（task_plan W3 任务 1）：models 定价表 CRUD——POST（创建 + 查重 409）+
GET（全量清单）+ PATCH（局部更新定价/启停）+ DELETE（硬删）。models 为全局表，
管理面经 JWT 鉴权即可操作，不做租户隔离。
"""

import pytest
from fastapi.testclient import TestClient
from decimal import Decimal

import app.api.admin.models as models_module
from app.main import create_app
from app.models import Model
from tests._fake_db import FakeSession


def _shared_factory():
    """返回持有单例 FakeSession 的 AsyncSession 工厂（跨请求共享状态）。"""

    def factory(*args, **kwargs):
        return factory.shared

    factory.shared = FakeSession()
    return factory


def _jwt_header():
    from app.core.security import create_access_token

    return {"Authorization": f"Bearer {create_access_token(sub='1', tenant_id=1)}"}


@pytest.fixture
def client(monkeypatch):
    """桩化 models 模块 AsyncSession（共享单例），挂真实 app。"""
    monkeypatch.setattr(models_module, "AsyncSession", _shared_factory())
    with TestClient(create_app(), raise_server_exceptions=False) as c:
        yield c


def _create_model(client, **overrides):
    # JSON body 无 Decimal 语义，请求体传 float；Pydantic(Decimal) 落库为 Decimal。
    payload = {
        "model_name": "qwen-plus",
        "channel_id": 1,
        "input_price": 0.001,
        "output_price": 0.002,
        "enabled": True,
    }
    payload.update(overrides)
    return client.post("/api/models", headers=_jwt_header(), json=payload)


def test_create_model_returns_pricing(client, monkeypatch):
    """POST /api/models：201 + 定价字段落库（model_name/进价/出价/启停）。"""
    factory = _shared_factory()
    monkeypatch.setattr(models_module, "AsyncSession", factory)
    with TestClient(create_app(), raise_server_exceptions=False) as c:
        resp = _create_model(c)
        assert resp.status_code == 201

    body = resp.json()
    assert body["model_name"] == "qwen-plus"
    assert body["channel_id"] == 1
    assert body["input_price"] == 0.001
    assert body["output_price"] == 0.002
    assert body["enabled"] is True

    stored: Model = factory.shared.stored(Model)[0]
    assert stored.model_name == "qwen-plus"
    assert stored.input_price == Decimal("0.001")
    assert stored.output_price == Decimal("0.002")


def test_create_duplicate_model_returns_409(client):
    """model_name 重复 → 409 conflict_error（查重式幂等，无唯一约束沿用 DW）。"""
    first = _create_model(client)
    assert first.status_code == 201
    dup = _create_model(client)
    assert dup.status_code == 409
    assert dup.json()["error"]["type"] == "conflict_error"


def test_create_model_requires_jwt(client):
    """无 JWT → 401 authentication_error（管理面契约）。"""
    resp = client.post("/api/models", json={"model_name": "x"})
    assert resp.status_code == 401
    assert resp.json()["error"]["type"] == "authentication_error"


def test_list_models_returns_items(client):
    """GET /api/models：全量清单含定价（含 disabled）。"""
    created = _create_model(client)
    assert created.status_code == 201

    resp = client.get("/api/models", headers=_jwt_header())
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["model_name"] == "qwen-plus"
    assert items[0]["enabled"] is True


def test_update_model_partial(client, monkeypatch):
    """PATCH：仅改 out_price + 下架，其余字段保留。"""
    factory = _shared_factory()
    monkeypatch.setattr(models_module, "AsyncSession", factory)
    with TestClient(create_app(), raise_server_exceptions=False) as c:
        created = _create_model(c)
        model_id = created.json()["id"]
        resp = c.patch(
            f"/api/models/{model_id}",
            headers=_jwt_header(),
            json={"output_price": 9.99, "enabled": False},
        )
        assert resp.status_code == 200

    body = resp.json()
    assert body["model_name"] == "qwen-plus"  # 未提供字段保留
    assert body["output_price"] == 9.99
    assert body["enabled"] is False
    assert body["input_price"] == 0.001


def test_update_missing_model_raises(client):
    """PATCH 不存在的 model → 404 model_not_found。"""
    resp = client.patch("/api/models/999", headers=_jwt_header(), json={"enabled": False})
    assert resp.status_code == 404


def test_delete_model_hard_deletes(client, monkeypatch):
    """DELETE /api/models/{id}：204，store 中移除（硬删）。"""
    factory = _shared_factory()
    monkeypatch.setattr(models_module, "AsyncSession", factory)
    with TestClient(create_app(), raise_server_exceptions=False) as c:
        created = _create_model(c)
        model_id = created.json()["id"]
        resp = c.delete(f"/api/models/{model_id}", headers=_jwt_header())
        assert resp.status_code == 204

    assert factory.shared.stored(Model) == []  # 已硬删


def test_delete_missing_model_raises(client):
    """DELETE 不存在的 model → 404。"""
    resp = client.delete("/api/models/999", headers=_jwt_header())
    assert resp.status_code == 404