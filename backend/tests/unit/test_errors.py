"""T008 错误出口冒烟：422/自定义异常/未捕获异常均重塑为 OpenAI 错误结构。"""

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.core.errors import ModelNotFoundError, register_error_handlers


class Req(BaseModel):
    model: str


def _build_app() -> FastAPI:
    """测试专用小应用：复用统一错误出口注册（不污染正式 app）。"""
    app = FastAPI()
    register_error_handlers(app)

    @app.post("/t")
    async def t(req: Req) -> dict:
        return {}

    @app.get("/model-not-found")
    async def m() -> dict:
        raise ModelNotFoundError()

    @app.get("/boom")
    async def boom() -> dict:
        raise RuntimeError("secret internal detail")  # 不应泄露给调用方

    return app


def test_validation_error_reshaped_to_openai():
    """422：缺字段 → invalid_request_error 结构。"""
    client = TestClient(_build_app())
    resp = client.post("/t", json={})
    assert resp.status_code == 422
    body = resp.json()["error"]
    assert body["type"] == "invalid_request_error"
    assert "model" in body["message"]  # 定位到缺失字段


def test_custom_error_reshaped_to_openai():
    """自定义异常（404 model_not_found）→ 契约结构。"""
    client = TestClient(_build_app())
    resp = client.get("/model-not-found")
    assert resp.status_code == 404
    body = resp.json()["error"]
    assert body["type"] == "invalid_request_error"
    assert body["code"] == "model_not_found"
    assert body["param"] == "model"


def test_unhandled_error_reshaped_without_stack():
    """500：未捕获异常 → api_error，不泄露内部信息。"""
    # raise_server_exceptions=False：让 ServerErrorMiddleware 转交统一出口而非直接抛出
    client = TestClient(_build_app(), raise_server_exceptions=False)
    resp = client.get("/boom")
    assert resp.status_code == 500
    body = resp.json()["error"]
    assert body["type"] == "api_error"
    assert "secret internal detail" not in body["message"]