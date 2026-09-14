# OneHub 发现与事实（findings）

> 只放跨会话仍有价值的研究结论与技术事实，不放任务状态（那在 task_plan.md）。

## 项目设置

- **spec-kit v1.0.1** 已通过 `specify init . --integration claude` 初始化：`.specify/`（模板/脚本/constitution）+ `.claude/skills/`（speckit-* 命令）
- feature 目录采用顺序编号（init-options.json: `feature_numbering: sequential`）→ W1 = `specs/001-openai-compat-gateway`
- `.specify/feature.json` 记录活跃 feature 目录，/speckit-plan、/speckit-tasks 靠它定位

## 技术事实（W1 相关，供实现阶段查阅）

- **OpenAI Chat Completions 协议核心契约**：请求 `{model, messages, stream, ...}`；非流式响应含 `id/object/choices/usage`；流式为 SSE，chunk 逐块推送，终止事件为 `data: [DONE]`
- **上游 usage 语义**：DeepSeek 等渠道在流式模式下默认不在 chunk 中带 usage，需调用方在请求中显式设置 `stream_options: {"include_usage": true}` 才在最后一个 chunk 返回——这正是 /clarify Q2 要定夺的行为（网关是否替调用方注入）
- **错误协议**：OpenAI 错误体为 `{error: {message, type, param, code}}`；4xx/5xx 语义与上游状态码对齐（429 限流、401 鉴权失败等）
- **协议兼容验证标准**：OpenAI 官方 SDK 只改 `base_url` + API Key，流式/非流式均正常（PROJECT_CONTEXT 唯一硬标准）

## 流程发现

- spec-kit 的 /speckit-specify 规定 [NEEDS CLARIFICATION] 标记最多 3 个；本 feature 用了 4 个（FR-004/005/008/010），已在 spec 内说明理由（Q4 建表范围影响脚手架任务拆分），等 /clarify 统一回收
- /speckit-clarify 上限 5 问；Asize 要求一次性列出（覆盖"逐问交互"默认流程），等亲自回答

## 技术事实（W3 计费相关）

- **JSON body 无 Decimal 语义**：`client.post(json=...)`（httpx/TestClient）用 stdlib json.dumps 序列化请求体，遇 `Decimal` 抛 `TypeError: Decimal is not JSON serializable`——请求体 price 必须传 float；落库为 Decimal 交给 Pydantic（字段类型 `Decimal` 会把 JSON float 转 Decimal），返回序列化再由端点 `_public_fields` 转 float。三层职责各归各：传输用 float、建模用 Decimal、展示用 float。
- **SQLAlchemy `Numeric` → SQLite/Postgres 语义**：models 定价字段用 Numeric 存 Decimal，网格成本 `estimate_cost` 全程 Decimal 计算避免浮点误差，仅写回/出网时转 float。

## 技术事实（W3 任务 2：用量事件链路）

- **Redis Stream 是可持久化消息队列偏好的关键**：网关把用量事件 XADD 进 `usage:events`，worker 用消费组（XREADGROUP + XACK）异步落 `usage_records`，事件先落 Redis 再异步入库——把"转发耗时"与"计费落库"解耦（削峰），请求链路不等待 DB 写。
- **Redis XADD 拒绝 None 字段值**（`DataError: Invalid input of type: 'NoneType'`）：字段值必须为 bytes/str/int/float。可选字段（channel_id/latency_ms）为 None 须在 emit 之前过滤，消费端用 `ev.get()` 缺省兼容——不能把 None 直接塞进 Stream。
- **`decode_responses=True` 读 Stream 让所有字段值变 str**：worker 建 `aioredis.Redis(..., decode_responses=True)` 后 XREADGROUP 返回的字段值全是 str（`'10'`、`'1'`），直接绑定 int 列报 asyncpg `DataError: 'str' object cannot be interpreted as an integer`——消费端需 `int()` 收敛（None/空串 → None）。Stream 本身天然是"全字节"模型，跨边界一律按 str 处理。
- **幂等落库的锚点 = `usage_records.request_id`（全局 unique）**：重复事件（同 request_id 线程重发 / 崩溃后 XACK 重放）被 DB unique 约束拒绝，配合消费端"先查已落库集合→跳重"双保险，任意重放零重复行。request_id 在网关每次真实转发分配，缓存回放/失败路径不产生事件（无用 log 污染）。
- **Celery task 内桥接 async 数据库**：`process_usage_events` 是同步 task 签名，内部 `asyncio.run()` 跑 async 引擎/session（SQLAlchemy async），临时引擎用完 `engine.dispose()`——worker 长驻进程不跨任务持有连接。

## 技术事实（W3 任务 3：乐观锁并发扣减）

- **乐观锁 CAS 单行互斥**：`UPDATE balances SET balance = balance - cost, version = version + 1 WHERE tenant_id = ? AND version = ?`——PostgreSQL 行级锁使每个 version 恰被一个并发请求消费，rowcount=0 即 version 已变（有并发者提交），重读最新 version 重试。balance 存绝对值（非 delta），重读时读到的是包含他人扣减的最新值，扣减正确，**零丢失**。
- **thundering herd（同波竞争）**：并发者同时读到同一 version 再同时 UPDATE，每波仅 1 人成功（其余 rowcount=0 进入重试），下一波再次同读同写——第 k 个并发者需约 k 次重试才能收敛。因此**重试上限必须 ≥ 预期并发峰值**（对账 50 并发验证 → 上限取 100 覆盖 + 余量），而非拍脑袋的小常数；冲突是瞬态（有并发者提交即收敛），重读即收敛，重试耗尽才抛错。
- **`version` 列在建表迁移 T006 已含**（`balances.version BigInteger server_default='0'`），乐观锁无需新迁移，不触发 DDL 停机点。
- **幂等落库与扣减的顺序性**：`UsageConsumer.consume` 先查已落库 request_id 集合跳重，再逐事件「插入 UsageRecord + charge_balance 扣减 + 失效 Redis 余额缓存」，全部同事务 commit——重复事件（同 request_id）零新增行也零重复扣减（DB unique 兜底）。
- **对账脚本三段语义（PROJECT_CONTEXT 6.3）**：① Stream 事件数 == usage_records 行数（幂等零重复）；② 余额 == 初始 − Σcost（Decimal 精确求和对账）；③ N 路并发 `charge_balance` 收敛（乐观锁 + 失败重试鲁棒性）。幂等重放（同 request_id 重复事件）零新增、余额不变为额外验证。

## 技术事实（W4 任务 1：智能路由 + 三态熔断）

- **三态熔断状态放 Redis（多 worker 共享）手写 Lua 原子迁移**：`HASH breaker:{channel_id}` 存 `state/failure_count/opened_at`，唯一共享点是状态（EVAL 内查询+迁移原子）；CLOSED 计数放行（失败才计数）/ OPEN 冷却期内拒绝、过冷却转 HALF_OPEN 放行探测 / HALF_OPEN 放行探测后据成败切回或重开。进程内 explain（闭包/纯函数）+ Redis 状态组合，不打散"手写保护清单"叙事。
- **候选=数据库可见渠道而非 provider 平面**：同一 `model_name` 的 model_enabled 多行各挂一个 channel → 每行一条 `Candidate(channel_id, weight=channel.weight, available=channel.status=="healthy")`。通道来源是 DB（权重/封禁/渠道归属都在这张表），不引入独立"渠道注册表"。
- **加权轮询 = 游标对"可用总权重"取模**：进程内 `WeightedRobin._cursor` 持续递增，落在哪个候选的累计权重区间即选中；weight 大的区间更宽 → 被选概率更高，确定且均匀（非纯随机，无长尾扎堆）。多 worker 下轮询游标各进程独立，仅熔断状态跨 worker 共享——负载均衡允许多 worker 分布（确定性让位给可用性）。
- **指数退避重试要配抖动（jitter）避免 thundering herd**：`delay = min(cap, base*2^(attempt-1)) * (1 ± jitter)`。纯退避在同波同时失败时会把重试再次撞在同一时刻；±15% 抖动散开。attempt 从 1 起。`rng` 可注入以便测试对拍确定性。
- **流式只在"首个事件产出前"可重试**：流式走 `pick_channel` 单次选择（选到即透传，已下发的 SSE 无法回滚重试）；非流式走 `forward`（加权挑选→失败退避→换候选，全部失败 503）。
- **熔断记成功/失败去驱动状态机**：`record_success`（HALF_OPEN 探测成功 → 重开 CLOSED、清零计数）/ `record_failure`（计数+超阈值 → OPEN 记 opened_at）。CLOSED 时计数放行但失败累计，避免"每次失败都直接开断"。
- **provider 按渠道惰性构造缓存**：`_provider_for_channel(channel)` 首建 `DeepSeekProvider(base_url=channel.base_url, api_key=channel.api_key_encrypted)` 入 `app.state.channel_providers`（连接池复用），lifespan 关闭统一 aclose。密钥沿用渠道表明文占位（W4 加密走 ADR，不本任务做）。
- **单渠道回退保兼容**：候选为空（model.channel_id=None/测试桩）时回退 `app.state.deepseek_provider` 单例，既有单渠道语义与 126 基线上所有网关测试零改动通过。

## 技术事实（W4 任务 2：React 管理台 + Vite proxy）

- **Vite 配置文件加载优先级会坑人**：`vite.config.ts` 与 `vite.config.js` 同时存在时，Vite 优先加载 **`.js`**（旧编译遗留物默认 target=8000 正式容器），`.ts` 里改 target 不生效 → dev proxy `/api/**` 转发到"未挂管理台路由"的 8000 返回 `{"detail":"Not Found"}`，直连 8001 却是 200。排查用 `DEBUG=vite:proxy` 起 vite 看 `vite:proxy /api/... -> http://127.0.0.1:8000` 立刻暴露真实目标。解法：删除遗留 `vite.config.js`，只保留 `.ts`。
- **管理台接口统一挂 `/api` 前缀 + require_admin JWT**：与网关面 sk-key 鉴权完全解耦——管理面 POST `/api/auth/login` 签 JWT，`require_admin` 校验 Bearer；前端 `api.ts` 把 token 存 `localStorage`（`onehub_admin_token`），请求头 `Authorization: Bearer`，401 清 token 回登录页。
- **熔断实时态只读暴露**：`CircuitBreaker.get_state(channel_id)` 只 `HGETALL` 读 Redis 不迁移任何状态——管理台可视化熔断三态用，避免"看仪表盘就改变熔断状态"的副作用。
- **Playground 代理复用智能路由非重复造链**：`/api/play/chat` 的候选收集/熔断过滤/加权轮询/退避重试/provider 惰性缓存全部走 `smart_router` + `_provider_for_channel`，仅换鉴权边界（JWT 而非 sk-key），不叠加限流/幂等/余额预检（调试语义）。
- **Playground 模型来源多行语义**：Playground 模型下拉取 `models` enabled 集合；同 model 名多行（主/备渠道各挂同名模型）在转发时都会成为候选——前端展示去重、后端按渠道候选转发，两处语义一致。
