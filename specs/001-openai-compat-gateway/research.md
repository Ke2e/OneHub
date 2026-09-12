# Research: OpenAI 协议兼容网关（W1）

> Phase 0 输出。记录技术决策：Decision / Rationale / Alternatives。所有 /clarify 已决项见 spec.md Clarifications 节，此处不重复，只补实现层决策。

## D1: Provider 接口形态（design-an-interface 两版候选对比）

**Decision**: 候选 B——模板方法基类（`providers/base.py` 的 `BaseProvider` ABC）。

```python
class BaseProvider(ABC):
    def __init__(self, base_url: str, api_key: str): ...
    @abstractmethod
    def _headers(self) -> dict[str, str]: ...
    async def chat(self, request: ChatCompletionRequest) -> ChatCompletionResponse: ...
    def chat_stream(self, request: ChatCompletionRequest) -> AsyncIterator[ChatCompletionChunk]: ...
```

通用 OpenAI 兼容转发逻辑（请求组装、SSE 逐行解析、usage 提取、错误映射）单点实现在基类；子类只填差异点（`DeepSeekProvider` 仅 `_headers` + base_url 常量）。

**Rationale**:
- 三大目标渠道（DeepSeek/通义/智谱）均为 OpenAI 兼容协议，差异集中在 base_url 与鉴权头——模板方法是适配器模式在此场景的自然形态（W1 teach 检查点要求能讲适配器模式）
- SSE 透传与 usage 解析是**保护清单组件**（手写 + TDD + 单源实现），放基类只写一次、只测一次；候选 A 会把解析逻辑挤到网关 services 层，provider 与转发职责纠缠
- W4 路由/熔断按渠道实例化 provider，接口稳定即可插拔

**Alternatives considered**:
- **候选 A（薄 Protocol + 工厂）**：`class LLMProvider(Protocol)` 结构化鸭子类型，每渠道独立实现，公共逻辑放 `services/forward.py`。被否：每新增渠道要在"provider 自含转发"与"网关层共享转发"之间再选一次，边界摇摆；usage 解析离 provider 太远
- **候选 C（零抽象）**：不搞 provider，一个 `OpenAICompatClient(base_url, key)` 吃遍渠道。被否：与 PROJECT_CONTEXT 强制目录结构（base.py + deepseek.py + qwen.py + zhipu.py）冲突。**诚实备注**：若 W4 发现通义/智谱子类真的零差异，可降级为纯配置实例化（届时记 ADR），W1 先按结构走

## D2: 渠道密钥的 W1 处理

**Decision**: 运行时密钥从 `DEEPSEEK_API_KEY` 环境变量注入（FR-006 原文）；DB `channels.api_key_encrypted` 存显式占位符（如 `env-injected`）满足 NOT NULL。

**Rationale**: 加密存储需要 `cryptography`（Fernet）等新依赖——技术栈清单外依赖 = 停机点 3，为 W1 的一行占位数据触发不值得。W4 管理台做渠道 CRUD 时再以 ADR 提案加密方案，届时把密钥从 env 迁入 DB，只改 provider 构造函数一处。

**Alternatives considered**:
- Fernet 加密落库（引 cryptography）：功能更完整但违停机点流程，否
- 存 SHA-256 不可逆哈希：字段名撒谎（要 encrypted 却不可逆），否

## D3: SSE 流式透传实现要点

**Decision**: `httpx.AsyncClient.stream()` + `aiter_lines()` 逐行产出，`StreamingResponse` 直通 `text/event-stream`，全链路不缓冲整段响应。

**Rationale / 关键事实**:
- OpenAI 流协议：每事件以空行分隔，`data: {json}` 逐块，终止符 `data: [DONE]`
- DeepSeek 流式默认不返回 usage，需请求体带 `stream_options: {"include_usage": true}` 才在最后一个 chunk 带 `usage`——网关对 `stream: true` 的入站请求**无条件注入**（Q2: A 决议），调用方零配置拿 usage
- usage 解析（从 chunk 流提取 usage 字段）与 delta 累计属保护清单：`services/forward.py` 手写 + 先写测试（TDD），用录制的 SSE fixture 回放断言
- 调用方断开：`StreamingResponse` 的 `request.is_disconnected()` / 取消传播终止上游流，不留孤儿连接
- 上游超时：httpx `timeout` 配置，超时返回 OpenAI 格式错误而非挂死

**Alternatives considered**: 无（实现细节层面，无候选分歧）。

## D4: 请求校验与错误映射

**Decision**: 入站请求体用 Pydantic 严格校验（缺 model/messages、类型错误 → 422 转 OpenAI 格式错误）；上游非 2xx → 映射为 `{error: {message, type, code}}`，状态码语义保留（上游 429 → 网关 429）；所有网关自身错误（401 鉴权失败等）同样走 OpenAI 错误结构。

**Rationale**: SC-004 要求 5 类异常请求零内部堆栈泄露；统一错误出口（FastAPI exception_handler）是最小实现。

**Alternatives considered**: 透传上游原始错误体——省事但非 OpenAI 协议格式且泄露上游信息，否。

## D5: compose 分期

**Decision**: W1 = api + pg + redis 三服务；W3 增 worker（Celery）；W4 增 nginx（压测/集群）。迁移 + 种子在 api 服务启动时由启动脚本执行（`alembic upgrade head && python scripts/seed.py`），保证"一条命令起全环境"（SC-003）。

**Rationale**: 最小改动原则——空转的 worker/nginx 服务是投机配置；种子幂等（upsert）保证重复启动无手工清理（User Story 5 场景 2）。

**Alternatives considered**: 一次建齐 5 服务：被否（见上）。
