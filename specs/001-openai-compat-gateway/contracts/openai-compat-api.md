# Contract: OpenAI 兼容 API（网关面）

> Phase 1 输出。W1 对外唯一契约 = OpenAI Chat Completions 协议的兼容子集。管理面端点属 W2，不在本契约。
> 权威参照：OpenAI Platform API 文档（Chat Completions / Models）。本文定义网关行为差异点。

## 鉴权（全部网关面端点）

- 请求头：`Authorization: Bearer <GATEWAY_API_KEY>`（`.env` 配置的固定管理 Key，W1 过渡方案，W2 换 SK- Key 体系）
- 失败响应（缺失/不符）：`401` + OpenAI 错误结构（见错误节）；比对用 constant-time

## POST /v1/chat/completions

### 请求（入站接受字段）

| 字段 | 类型 | 必填 | 网关行为 |
|------|------|------|---------|
| model | string | 是 | 必须在 models 表 enabled 集合内 |
| messages | array | 是 | OpenAI 格式，透传 |
| stream | boolean | 否 | 缺省 false；true 走 SSE |
| temperature / top_p / max_tokens / stop / presence_penalty / frequency_penalty / n / ... | | 否 | 透传 |
| stream_options.include_usage | object | 否 | 网关**无条件注入 true**（见下），入站传不传都行 |

**网关注入规则（Q2: A 决议）**：`stream: true` 时，网关在转发上游前强制设置 `stream_options: {"include_usage": true}`——调用方零配置在流末尾 chunk 拿到 usage。

### 非流式响应

标准 OpenAI Chat Completion 对象：`id`、`object: "chat.completion"`、`created`、`model`、`choices[]`（含 `message`/`finish_reason`）、`usage: {prompt_tokens, completion_tokens, total_tokens}`。HTTP 200。

### 流式响应

- Content-Type: `text/event-stream`
- 逐块 `data: {chunk}`，chunk 为标准 OpenAI 流对象（`object: "chat.completion.chunk"`，`choices[].delta`）
- 最后一个数据 chunk 携带 `usage`（上游注入 include_usage 的结果）；随后 `data: [DONE]` 终止
- 上游中断/超时：以协议兼容方式终止流，不挂死调用方
- 上游异常未返 usage：usage 缺省 0，不向调用方报错

## GET /v1/models

标准 OpenAI 模型列表：`{object: "list", data: [{id, object: "model", created, owned_by}]}`，数据源 = models 表 enabled 集合（Q3: B 决议）。

## 错误契约（所有非 2xx）

```json
{
  "error": {
    "message": "...",
    "type": "invalid_request_error | authentication_error | rate_limit_error | ...",
    "param": "model | null",
    "code": "model_not_found | ... | null"
  }
}
```

| 场景 | HTTP | type |
|------|------|------|
| 请求体缺字段/类型错误 | 422 | invalid_request_error |
| model 不在可用集 | 404 | invalid_request_error（code: model_not_found） |
| Bearer Key 缺失/错误 | 401 | authentication_error |
| 上游限流 | 429 | rate_limit_error（语义保留） |
| 上游不可用/超时 | 502/504 | api_error（OpenAI 格式，零内部堆栈） |

**硬约束**：任何错误出口（含 FastAPI 默认 422/500）都重塑为上述结构——SC-004 验收点。

## 兼容性判定标准（唯一验收口径）

OpenAI 官方 JS/TS SDK 仅改 `base_url` 与 `apiKey`，非流式/流式/usage/模型列表全部正常——SDK 侧零改动零报错。
