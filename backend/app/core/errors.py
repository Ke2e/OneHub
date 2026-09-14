"""统一错误出口（T008）：所有非 2xx 响应重塑为 OpenAI 错误结构。

契约（contracts/openai-compat-api.md 错误节）：
    {"error": {"message", "type", "param", "code"}}

- 自定义异常 OpenAIError（及其子类）→ 按携带的 status/type 输出
- FastAPI 校验错误（422）→ type: invalid_request_error
- 未捕获异常（500）→ type: api_error，零内部堆栈泄露
"""

from typing import Any, Optional

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class OpenAIError(Exception):
    """网关业务异常基类：异常属性决定响应体，无状态泄漏。"""

    def __init__(
        self,
        message: str,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        error_type: str = "invalid_request_error",
        param: Optional[str] = None,
        code: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.error_type = error_type
        self.param = param
        self.code = code


class AuthenticationError(OpenAIError):
    """Bearer Key 缺失/错误 → 401。"""

    def __init__(self, message: str = "missing or invalid API key") -> None:
        super().__init__(
            message=message,
            status_code=status.HTTP_401_UNAUTHORIZED,
            error_type="authentication_error",
        )


class ModelNotFoundError(OpenAIError):
    """请求 model 不在可用集 → 404（code: model_not_found）。"""

    def __init__(self, param: str = "model") -> None:
        super().__init__(
            message="model not found",
            status_code=status.HTTP_404_NOT_FOUND,
            error_type="invalid_request_error",
            param=param,
            code="model_not_found",
        )


class RateLimitError(OpenAIError):
    """上游限流 → 429（语义保留给调用方）。

    retry_after（可选）：指定时响应携带 `Retry-After` 头（W2 任务 3 网关自身限流，
    告知调用方重试等待秒数）。
    """

    def __init__(
        self,
        message: str = "rate limit exceeded",
        retry_after: Optional[int] = None,
    ) -> None:
        super().__init__(
            message=message,
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            error_type="rate_limit_error",
        )
        self.retry_after = retry_after


class ConflictError(OpenAIError):
    """资源冲突（W2 管理面：register 重复 email 等）→ 409。"""

    def __init__(self, message: str = "resource already exists") -> None:
        super().__init__(
            message=message,
            status_code=status.HTTP_409_CONFLICT,
            error_type="conflict_error",
        )


class InsufficientBalanceError(OpenAIError):
    """租户余额不足（W3 任务 1 余额预检）→ 402（OpenAI 生态支付语义）。"""

    def __init__(
        self,
        message: str = "insufficient balance",
        code: str = "insufficient_balance",
    ) -> None:
        super().__init__(
            message=message,
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            error_type="insufficient_quota",
            code=code,
        )


class UpstreamError(OpenAIError):
    """上游不可用/超时 → 502/504，OpenAI api_error 结构、零内部堆栈。"""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(
            message=message,
            status_code=status_code,
            error_type="api_error",
        )


def _body(message: str, error_type: str, param: Optional[str], code: Optional[str]) -> dict[str, Any]:
    """组装 OpenAI 错误结构；param/code 缺省为 null。"""
    return {
        "error": {
            "message": message,
            "type": error_type,
            "param": param,
            "code": code,
        }
    }


async def _openai_error_handler(request: Request, exc: OpenAIError) -> JSONResponse:
    """自定义异常 → 直接映射；401 带 WWW-Authenticate，限流（带 retry_after）带 Retry-After。"""
    headers: dict[str, str] = {}
    if exc.status_code == status.HTTP_401_UNAUTHORIZED:
        headers["WWW-Authenticate"] = "Bearer"
    if isinstance(exc, RateLimitError) and exc.retry_after is not None:
        headers["Retry-After"] = str(exc.retry_after)
    return JSONResponse(
        status_code=exc.status_code,
        content=_body(exc.message, exc.error_type, exc.param, exc.code),
        headers=headers or None,
    )


async def _validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """FastAPI 422 校验错误 → invalid_request_error（取首条错误信息）。"""
    first = exc.errors()[0]
    location = ".".join(str(p) for p in first.get("loc", []) if p not in ("body", "query", "path"))
    message = f"{location}: {first.get('msg', 'invalid request')}" if location else first.get("msg", "invalid request")
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=_body(message, "invalid_request_error", None, None),
    )


async def _unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """兜底 500 → api_error，message 通用文案防堆栈泄漏。"""
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=_body("internal server error", "api_error", None, None),
    )


def register_error_handlers(app: FastAPI) -> None:
    """全局异常处理器注册：must be called after create_app。"""
    app.add_exception_handler(OpenAIError, _openai_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(Exception, _unhandled_error_handler)