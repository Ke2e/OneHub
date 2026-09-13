# OneHub 周学习笔记（W1）

> 首次使用场景创建（T027，W1 收尾）。以 W1 实际实现为纲沉淀每周学习要点；teach 检查点材料同源维护，
> 讲解验收标准（W1 任务 4）见 docs/task_plan.md 阶段总览。

## 2026-09-12 周 · W1：协议兼容网关五讲

### 1. FastAPI async 应用生命周期

**应用工厂 + lifespan**（`backend/app/main.py`）：

- `create_app()` 工厂函数按需组装 app，测试里 `TestClient` 与生产 `uvicorn` 共用同一构造路径
- `@asynccontextmanager lifespan` 在启动/关闭时各执行一次：**异步引擎（AsyncEngine）与客户端连接池的生命周期由应用持有**——启动建池、关闭 `engine.dispose()`，避免进程级资源泄漏
- 连接池用 `pool_pre_ping=True`：取出连接前先 ping，防"数据库重启后连接池里躺着死连接"这类经典坑（默认惰性失效要在请求失败后才暴露）

**async 为什么能扛高并发（面试关键）**：

- uvicorn 是单进程多协程事件循环；`await` 让出 CPU 等 I/O 时，事件循环去跑别的协程——Gateway 的核心 I/O（DB 查询、上游 HTTP 转发）全是等待，天然契合
- **陷阱**：async 请求里不能跑 CPU/阻塞调用（如同步 `requests`、`time.sleep`），否则整环阻塞。httpx 必须用 AsyncClient

### 2. Pydantic v2 / OpenAPI 契约

**数据契约价值**：OpenAI 协议兼容的本质是"字节级对得上"——Pydantic 模型就是契约的强制执行点（`backend/app/schemas/chat.py`）。

- `ChatCompletionRequest` 只声明必用字段 + `extra="allow"` 透传未知字段：**网关向前兼容新模型/新参数，不用每次升级 SDK 都改 schema**
- `response_model=ChatCompletionResponse` 强制响应结构；`exclude_none` 保证空字段不污染响应体
- FastAPI 的 OpenAPI 文档（/docs）自动由这些 schema 生成——协议文档零成本同步

### 3. 适配器模式（Adapter Pattern）：Provider 模板方法基类

**问题**：多模型接入时，上游 API 细节不同，但网关侧的使用方式必须统一。

**解法**（`backend/app/providers/base.py` + `deepseek.py`，research.md D1 候选 B 两版对比后定稿）：

- `BaseProvider` ABC 定义**模板方法** `chat()` / `chat_stream()`：连接池、URL 规整（`_normalize_base_url()` 自动补 `/v1`）、错误映射、响应结构校验全在基类固化
- 子类只实现**差异点**（抽象方法 `_headers()`：Bearer 头）——"封装变化点，复用不变点"
- **面试讲法**：换新渠道 = 新建一个 Provider 子类，网关主链路零改动；这是接口隔离类模式，比 if-else 分渠道优雅在"新增渠道不再碰已有代码"（开闭原则）

### 4. httpx：异步 HTTP 客户端与流式上游

- `httpx.AsyncClient` 连接池（`limits` 控制最大连接/等待数），进程级复用（app.state 单例）——每次请求新建 client 会空转 TCP/TLS 握手
- `client.stream("POST", url, json=...)` 返回响应流：**不读完整个 body 就逐段读**，是 SSE 转发的基石
- 错误面：`client.stream()` 需 `async with` 管理生命周期；上游超时/连接失败在 provider 内映射为 OpenAI 503/504 结构（T011 `_map_upstream_error`）

### 5. SSE 流式转发：状态机 + 异步适配（保护清单，手写）

**SSE 协议**：`data: {json}\n\n` 为一条事件；`data: [DONE]` 为终止信号。

**双形态设计**（`backend/app/services/forward.py`，T015/T016/T018/T019）：

- 同步核心 `SSELineParser`：`feed()` 增量喂行 → 空行结算完整事件 → `parse_full()` 拿已结算事件。**状态机手写的价值**：坏 JSON 事件跳过（防御 SDK 侧崩）、EOF 无 [DONE] 自然收敛（上游突然中断不挂死调用方）
- 异步适配 `sse_events()`：`async for line in resp.aiter_lines()` 直喂同步核心——同步核心保单测直白（T015 全走同步），异步适配保线上吞吐
- `UsageTracker` + `with_usage()`：原样透传事件（保 delta 语义）+ 顺手提取 usage；上游不返 usage 时 `finish()` 合成 `usage=0` 兜底 chunk（位次=流尾最后一个，gateway 的 [DONE] 之前），保证调用方**零配置**拿到 usage（SC-002）
- 端点侧 `StreamingResponse` + `Cache-Control: no-cache` + `X-Accel-Buffering: no`：逐事件 yield 不缓冲整段（SC-005 首字节即时性），[DONE] 收尾由 `_stream_events` 补

**面试讲法**：SSE 转发三件事——解析（状态机）、透传（异步适配+生成器）、协议件（[DONE]/usage 收尾），三者各司其职，坏 JSON 与上游中断都不致命。

## W2 学习预告

多租户鉴权（Key 哈希/白名单）、令牌桶限流（Redis 原子性）、幂等键——W2 任务 5 检查点材料届时追加。
