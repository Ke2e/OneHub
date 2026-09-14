"""W4 任务 2：管理面 Playground 代理端点离线测试。

策略（Asize 拍板：代理转发 + JWT）：
- require_admin JWT 鉴权（无 JWT → 401）
- model 不在 enabled 集合 → 404 model_not_found
- 非流式/流式转发复用网关智能路由：
  - 无候选（model.channel_id 为 None，测试桩语义）→ 回退 app.state.deepseek_provider
  - 有候选 → 走 smart_router（此处聚焦单渠道回退路径；多渠道智能路由在
    test_gateway_router 已覆盖，play 不重复）
- FakeProvider 桩：chat/chat_stream 返回固定结构，替换 app.state.deepseek_provider
"""

import pytest
from fastapi.testclient import TestClient

import app.api.admin.play as play_module
from app.main import create_app
from app.models import Model
from tests._fake_db import FakeSession


def _jwt_header():
    from app.core.security import create_access_token

    return {"Authorization": f"Bearer {create_access_token(sub='1', tenant_id=1)}"}


class _FakeProvider:
    """Provider 桩：chat 返回 OpenAI 结构，chat_stream 产固定 chunk。"""

    def __init__(self, content="play-ok"):
        self.content = content
        self.chat_calls = 0

    async def chat(self, req):
        self.chat_calls += 1
        return {
            "id": "chatcmpl-play",
            "object": "chat.completion",
            "created": 0,
            "model": req.model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": self.content}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    async def chat_stream(self, req):
        yield {
            "id": "chatcmpl-play",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": req.model,
            "choices": [{"index": 0, "delta": {"content": "hi"}}],
        }

    async def aclose(self):
        """lifespan 退出会统一 aclose app.state 单例，桩 no-op。"""
        return None


def _shared_factory(initial):
    def factory(*args, **kwargs):
        return factory.shared
    factory.shared = FakeSession(initial=initial)
    return factory


@pytest.fixture
def play_env(monkeypatch):
    """stub play 模块 AsyncSession + 替换 app.state.deepseek_provider。"""

    def _make(initial):
        provider = _FakeProvider()
        monkeypatch.setattr(play_module, "AsyncSession", _shared_factory(initial))
        with TestClient(create_app(), raise_server_exceptions=False) as c:
            c.app.state.deepseek_provider = provider
            yield c, provider

    return _make


def _payload(model="m1", stream=False):
    return {"model": model, "messages": [{"role": "user", "content": "hi"}]} | (
        {"stream": True} if stream else {}
    )


def test_play_requires_jwt(play_env):
    """无 JWT → 401 authentication_error（管理面契约）。"""
    for c, _p in play_env([Model(model_name="m1", channel_id=None, enabled=True)]):
        resp = c.post("/api/play/chat", json=_payload())
        assert resp.status_code == 401


def test_play_model_not_enabled_404(play_env):
    """model 不在 models 表 enabled 集合 → 404 model_not_found。"""
    for c, _p in play_env([Model(model_name="m1", channel_id=None, enabled=True)]):
        resp = c.post("/api/play/chat", headers=_jwt_header(), json=_payload(model="ghost"))
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "model_not_found"


def test_play_non_stream_uses_provider(play_env):
    """非流式（无候选）→ 复用 app.state.deepseek_provider，返回 OpenAI 结构。"""
    for c, provider in play_env([Model(model_name="m1", channel_id=None, enabled=True)]):
        resp = c.post("/api/play/chat", headers=_jwt_header(), json=_payload())
        assert resp.status_code == 200
        body = resp.json()
        assert body["object"] == "chat.completion"
        assert body["choices"][0]["message"]["content"] == "play-ok"
        assert provider.chat_calls == 1


def test_play_stream_returns_sse(play_env):
    """流式（无候选）→ 200 text/event-stream，含 data 事件 + [DONE] 收尾。"""
    for c, provider in play_env([Model(model_name="m1", channel_id=None, enabled=True)]):
        resp = c.post("/api/play/chat", headers=_jwt_header(), json=_payload(stream=True))
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        text = resp.text
        assert "chat.completion.chunk" in text
        assert "data: [DONE]" in text