# OneHub 会话日志（progress）

## 会话 2026-09-14（W3 任务 3：并发扣减——乐观锁 + 失败重试达成，126 passed + 对账脚本四段 ALL PASS）

**背景**：W3 任务 3 = 并发扣减（乐观锁 + 失败重试），验收标准"对账脚本通过（PROJECT_CONTEXT 6.3 标准）"。保护清单项，dev-tdd 先写测试。`balances.version` 列 T006 建表时已含，无 DDL 变更，不触发停机点 3。

**做了什么**：

1. `app/services/billing.py`：`charge_balance`（乐观锁 CAS——先读最新 version → `UPDATE ... WHERE tenant_id=? AND version=?`，单行事务级互斥，rowcount=0 重读重试；无余额行/重试耗尽抛 `ChargeConflictError` 交 worker 回滚重试，不静默丢钱）+ `compute_cost` 倍率计价（实际 token×单价，Decimal 精确）+ `MAX_CHARGE_RETRIES`。
2. `app/services/usage_events.py`：`UsageConsumer.consume` 幂等落库后逐事件扣减租户余额（`charge_balance`）并失效 Redis 余额缓存；`_price_for`/`_tenant_for` 查价与租户归属。
3. `app/workers/billing.py`：`read_and_process`/`process_usage_events` 传 redis 连接给 `UsageConsumer`（扣减后失效缓存）。
4. 测试：`tests/unit/test_charge_billing.py`（CAS 重试/连续冲突耗尽/无行抛错/倍率计价）+ `tests/_fake_db.py` execute 改 async + BindParameter 取 .value。
5. 对账脚本 `scripts/reconcile_charge.py`：三段 + 幂等重放（db15 Redis 隔离，固定命名幂等可重跑）。

**验证（dev-verify 证据）**：

- `uv run pytest -q` → **126 passed**（116 基线 + 10 新增，全绿）
- **对账脚本（真 Redis+PG，PROJECT_CONTEXT 6.3 标准）四段 ALL PASS**：
  - ① 幂等落库零重复：XADD 50 唯一事件 → 消费落库 → Stream 事件数=50 == 行数=50（去重=50）
  - ② 倍率计价 + 乐观锁扣减：余额=974.8100 == 初始 1000.0000 − Σcost=25.190000（精确一致）
  - 幂等重放：同 request_id 重复事件落库 0 新行、余额不变（DB unique 兜底零重复扣减）
  - ③ 并发扣减收敛：50 路 asyncio.gather 并发 charge_balance → 余额=924.8100 == 974.8100 − 50.00（零丢失零重复扣减）
- **对账暴露并修复真实缺陷**：重试上限 5 在 50 路并发下抛 `ChargeConflictError after 5 retries`——同波并发者每波仅 1 人读到未被消费的 version（thundering herd），第 k 个并发者需约 k 次重试；上限提至 100（覆盖 50 并发 + 余量，冲突瞬态重读即收敛），重跑 ALL PASS。

**提交链**：`1a9dbe3`（test RED）→ `d1233b9`（feat GREEN）→ `058c89f`（fix 重试上限）→ `7543e2d`（test reconcile）

**下一步**：W3 任务 4 teach 检查点（幂等、事务隔离、并发扣减三方案、削峰，材料随任务产出待集中自验）。

## 会话 2026-09-14（W3 任务 2：用量事件 → Redis Stream → Celery 异步落库达成，116 passed + 真冒烟零重复）

**背景**：W3 任务 2 = 用量事件 → Redis Stream → Celery 异步落库，验收标准"链路通畅"。ADR-0002 已获 Asize 批准（方案 A：Celery，停机点 3 解除），本轮引入 `celery[redis]`。

**做了什么**：

1. `app/services/usage_events.py`：`USAGE_STREAM`/`USAGE_GROUP` 常量 + `build_usage_event` 纯函数（幂等锚点 request_id + 落库字段全集）+ `UsageProducer.emit`（XADD）+ `UsageConsumer.consume`（先查已落库 request_id 集合→幂等跳重→插入→commit，DB unique 兜底零重复）。
2. `app/workers/billing.py`：Celery app（redis broker/backend）+ `process_usage_events` task（asyncio 桥接 async 引擎/session，失败重试 max_retries=2）+ `read_and_process`（XGROUP_CREATE 幂等建组 → XREADGROUP 拉批 → `UsageConsumer` 幂等落库 → XACK）。
3. 网关接入：非流式（response.usage 非空）与流式（流尾 usage chunk 解析后）两路径在转发生效后 `emit`；缓存回放/失败不产生事件。lifespan 注入 `usage_producer` 单例（独立 Redis 连接，aclose 配对）。
4. docker-compose 增 `worker` 服务（复用 api 镜像，`celery -A app.workers.billing:celery_app worker`，depends_on pg+redis）。
5. 测试：`tests/_fake_usage.py`（FakeUsageProducer）+ `tests/unit/test_usage_events.py`（载荷/发射/消费幂等）+ 网关三条用 FakeUsageProducer 断言的改造。

**验证（dev-verify 证据）**：

- `uv run pytest -q` → **116 passed**（W3 任务 1 末 111 基线 + 5 新增，全绿）
- **真 Redis+PG 链路冒烟（链路通畅实证）**：向 `usage:events` XADD 11 事件（10 唯一 + 1 重复 request_id）→ `read_and_process` 消费落库 → 对账 `usage_records`：范围行数=10、distinct=10、插入=10（**零重复**）。命令级输出：`[audit] 本次注入范围行数=10（期望 10），去重行数=10（期望 10）→ PASS 零重复`
- 冒烟暴露并修复两个真实生产缺陷：① XADD 拒绝 None 字段（网关流式 `channel_id` 可 None）→ emit 落流前过滤；② worker `decode_responses=True` 读出全字段为 str → `_as_int` 收敛 int/None

**下一步**：W3 任务 3 并发扣减（乐观锁 + 失败重试，对账脚本，保护清单）。

## 会话 2026-09-14（W3 任务 1：计费引擎——models 定价表 CRUD + 余额预检达成，111 passed）

**背景**：W3 计费引擎任务 1 = models 定价表 CRUD + 余额预检（Redis，不查库，不足返 402）。Asize 跳过澄清，按推荐默认推进：新增 `InsufficientBalanceError`（402）、预检做成本估算（balance < 预估成本才 402）、P1 本轮跳过并入收尾前统一加固。

**做了什么**：

1. `InsufficientBalanceError`（app/core/errors.py）：继承 OpenAIError，`status_code=402`、`error_type="insufficient_quota"`、`code="insufficient_balance"`（OpenAI 生态支付语义，配 C278 quota 语义一致性）。
2. `app/services/billing.py`：`BALANCE_KEY`（Redis 键格式）+ `estimate_cost` 纯函数（`input_price×input_tokens + output_price×output_tokens`，缺省 output pricetokens 用 MAX_OUTPUT_TOKENS 兜底）+ `BalanceService.precheck`——先查 Redis 缓存，未命中查库回填（TTL 短缓存），`balance < cost` 抛 402；lifespan 实例化单例挂 `app.state.billing`。
3. 网关接入（gateway.chat_completions）：`get_billing` 依赖；在幂等检查后、并发槽获取前调用 `precheck`（估 token 用 messages 长度，成本未命中直接 402 不转发）。
4. 管理面 models 定价 CRUD（app/api/admin/models.py）：GET 全量清单 / POST 创建（model_name 查重→409）/ PATCH 局部更新 / DELETE 硬删（usage_records.model 为 VARCHAR 非 FK，删安全）；`_public_fields` 将 Numeric-Decimal → float 以 JSON 序列化；schemas 增 `ModelCreate` / `ModelUpdate`（price 字段为 Decimal）。

**验证（dev-verify 证据）**：

- `uv run pytest -q` → **111 passed**（W2 末 92 基线 + 19 新增，全绿；W3 任务 1 用例：billing 单测 + gateway_billing 端点 402/正常流 + admin_models CRUD 8 例）
- 排障一例：admin_models 首次全量报 `Decimal is not JSON serializable`——非端点 bug，是**测试请求体传了 `Decimal` 对象**，httpx 客户端序列化 JSON body 时失败（JSON 本无 Decimal 语义）；改为请求体传 float、由 Pydantic(Decimal) 落库为 Decimal，断言仍比对 Decimal。
- 提交链：`<feat security>` + `<test security>` + `<docs>`（见下 git 记录，按 W2 既定 docs(adr)→feat→test→docs 顺序）

**下一步**：W3 任务 2 用量事件 → Redis Stream → Celery 异步落库。

## 会话 2026-09-13（W2 任务 6：简历上墙点 1 + security-best-practices 审查达成，W2 阶段交付收口）

**背景**：任务 4 完成（92 passed）、任务 5 teach 材料产出。任务 6 目标 = 简历上墙点 1 初版 + security-best-practices 审查报告。

**做了什么**：

1. security-best-practices 主动审计（docs/security_review_w2.md）：按 skill 的 FastAPI 安全规范（OWASP 对齐）逐规则核查 W1–W2 全部代码 + 部署 + 依赖锁——审计范围 10 个入口文件（app 工厂/鉴权/管理面/网关面/限流/幂等/错误出口/部署/锁文件）+ uv.lock 版本取证（starlette 1.6.0 / fastapi 0.141.1 / uvicorn 0.52.4，均远超历史 CVE 修复线）
2. 审查结论：**14 项规则通过**（部署禁 reload/debug、鉴权统一依赖、Bearer 无 URL 令牌、PBKDF2+盐+常时比对、JWT 算法白名单、对象级租户隔离、无 cookie 无 CSRF 面、schema 化输入、SQL/命令注入零面、SSRF 无可控 URL、CORS 未放通、依赖补丁、密钥红线）——**未发现 Critical/High 可利用漏洞**；发现 2 项 Medium（/docs 管理面公开暴露、Redis 无 requirepass 且 6379 全主机暴露）+ 6 项 Low/观察（secret_key 默认值 change-me 缺守卫、无请求体大小上限、无安全响应头、PBKDF2 迭代 100k<OWASP 600k、JWT TTL 12h 无角色细分、LoginRequest 无长度下限）；保护清单四项（令牌桶/幂等/SSE/usage）核查均无注入面，**无换库简化类建议需要处理**
3. 简历上墙点 1 初版（docs/resume_draft.md）：W1–W2 成果四块（① OpenAI 协议兼容网关链路含手写 SSE/usage ② 多租户 SK-Key 鉴权体系 [手写] ③ Redis 手写令牌桶 + 并发控制 [手写] ④ 幂等键防重复转发 [手写]）+ 量化证据表（92 passed、真机 200/流式逐块透传、限流+幂等 10 项 IO 冒烟 ALL_PASS、npm verify 5/5、一键部署幂等），数字全部有命令级证据不虚构；含面试 K/A/P 深挖准备指针与 W3/W4 待办（压测数字入上墙点 2）

**验证（dev-verify 证据）**：

- `uv run pytest -q` → **92 passed**（回归确认，无代码改动）
- 审查报告与简历初版已落盘 docs/
- 提交：本会话随 docs 提交（见下）

**下一步**：W2 阶段 1-4/6 交付收口（任务 5 teach 讲解按 Asize 拍板延后集中自验）。进 W3 计费引擎——任务 1 models 定价表 CRUD + 余额预检（Redis，不查库，不足返 402）。

## 会话 2026-09-13（W2 任务 4：幂等键达成，92 passed + 真 Redis 冒烟 5 项 IO ALL_PASS）

**背景**：任务 3 完成（79 passed）。任务 4 目标 = 幂等键（Idempotency-Key）——重复请求不重复转发（Redis 存储，手写，保护清单）；dev-tdd 先写测试。与 api_keys 表无关，纯 Redis 层。

**做了什么**：

1. TDD 任务 4：
   - RED `test_idempotency.py`（7 用例）+ `test_gateway_idempotency.py`（2 用例）：三态判定（cached 回放 / claimed 轮询 / acquired 得主）、轮询接管（得主写缓存回放 / 得主撤销占位转接管 / 超时 409 conflict_error）、complete 写缓存 EX=24h + 删占位、cancel 仅删占位不写缓存；端点集成用 FakeRedis + `dependency_overrides` 换 FakeIdempotency（回放不转发 / 槽满 429 撤销占位可重试）
   - GREEN `app/services/idempotency.py`：手写 `IDEMPOTENCY_LUA`（KEYS[1]=缓存键 / KEYS[2]=占位键，ARGV=uuid + claim TTL；GET 缓存 → GET 占位 → SET NX 语义占位，EVAL 内 read-modify-write 原子——并发同 key 共享同一 Redis，脚本执行期间无并发间隙，防双转发）；`IdempotencyService.get_or_acquire` 三态入口（CLAIMED 内部短轮询消化不外显）、`_wait_result`（轮询缓存可回放 / 占位消失接管转发 / 超时 5s 抛 ConflictError 409）、`complete`（写缓存 EX=24h + 删占位）、`cancel`（删占位不写缓存——失败/429 释放可重试）、`aclose`
   - 端点接入（gateway.chat_completions，限流后、转发前）：仅非流式生效（流式 SSE 不缓存），`Idempotency-Key` 头 → get_or_acquire；CACHED 直接回放（不转发不占并发槽）；已在上游抛错路径（except BaseException）与 try_acquire 槽满分支补 `cancel` 释放占位；转发成功 → `complete`；main lifespan 增 `IdempotencyService` 单例（独立 Redis 连接，aclose 与 rate_limiter 配对）
2. 测试修正（单独 fix(test) 提交）：Lua 真实返回 `'claimed'` 对齐测试预期（首写 `'claim'` 不一致）；FakeRedis 模拟占位键写入语义；ApiKey fixture 补显式 `id=1`（鉴权走 scalar 不 commit，自增分配不会发生）；complete 断言改查 FakeRedis.data

**验证（dev-verify 证据）**：

- `uv run pytest -q` → **92 passed**（79 基线 + 13 新增，全绿；任务 4 用例 9 + 相关调整冗余消除）
- 真 Redis 冒烟（`smoke_idempotency.py` 用完即删，onehub-redis 容器 db 15 隔离）：**IDEMPOTENCY_SMOKE ALL_PASS**——IO1 新 key 得主 → complete → 同 key 回放缓存（body 一致）；IO2 **10 并发同 key 恰 1 得主其余 9 回放同一响应（防双转发核心实证）**；IO3 得主 cancel → 占位消失 → 后续请求重新占位成功；IO4 Lua 纯脚本语义（缓存命中立即 cached 且不写占位键）
- 提交链：`46105bc`（test RED）→ `3bcbf2e`（feat GREEN）→ `951459e`（fix test 对齐 claimed 语义 + 显式主键）

**下一步**：W2 任务 5 teach 材料（已随本会话产出至 notes.md 五讲）+ 任务 6 简历上墙点 1 + security 审查。

## 会话 2026-09-13（W2 任务 3：令牌桶 Lua + Semaphore 并发限流达成，79 passed）

**背景**：任务 2 完成（58 passed）。任务 3 目标 = 令牌桶（Redis Lua 手写，保护清单）+ Semaphore 并发限流——超限 429 + Retry-After，并发无竞态；dev-tdd 先写测试。

**做了什么**：

1. TDD 任务 3：
   - RED `test_rate_limit.py`（16 用例）+ `test_gateway_rate_limit.py`（4 用例）：Lua 公式 `_refill` 纯函数对拍锚点（封顶/比例/时钟倒退/首用）、TokenBucket ARGV 组装与结果 cast（`[1,0]`→(True,0) / `[0,wait]`→(False,wait)）、双维度合并（RPM 拒绝短路不查 TPM / TPM 拒绝 / tpm_limit=0 只查 RPM）、Semaphore max_concurrent 上限与 acquire-release 配对（release 后可再进）、estimate_tokens 粗估（字符 //4 兜底 1）；端点集成用 FastAPI `dependency_overrides` 换 FakeLimiter（monkeypatch 对路由注册期捕获的依赖引用无效，首跑 500 实测根因）
   - GREEN `rate_limit.py`：手写 `TOKEN_BUCKET_LUA`（KEYS=rate:{key_id}:{dim}，ARGV=capacity/rate/s/now/requested；HSET t+ts + EXPIRE 3600；有 token → 扣减返 {1,0}，无 token → 返 {0,wait}，wait=ceil((requested-t)/rate) 兜底 1s——EVAL 内 read-modify-write 原子，多请求共享同桶零竞态）；`TokenBucket.allow` 封装 EVAL + 返回值解析；`RateLimiter.check` 双维度合并短路 + `try_acquire/release` 并发槽——自维护计数 `_in_flight`（asyncio.Semaphore 无非阻塞原语，单线程协作调度下检查→自增无 await 间隙，等价 try-acquire 语义）+ `estimate_tokens`（转发前粗估，真实 usage 计费 W3）
   - 端点接入：`RateLimitError` 扩展 `retry_after` → `_openai_error_handler` 输出 `Retry-After` 头；gateway.chat_completions 在转发前 check（桶拒/槽满均 429）；流式 `_stream_events` finally 释放槽位（断连不泄漏），非流式 try/finally 即释；main lifespan 注入 `aioredis.Redis.from_url(decode_responses=True)` 单例，aclose 配对
2. 测试基建修复：conftest.py 删弃用的 session 级自定义 `event_loop` fixture（pytest-asyncio 0.26 弃用，pyproject 已设 `asyncio_default_fixture_loop_scope="function"`）→ 消除 async 测试偶发 `RuntimeError: no current event loop`

**验证（dev-verify 证据）**：

- `uv run pytest -q` → **79 passed**（58 基线 + 21 新增，全绿两次复跑稳定；此前"58 passed"日志为旧运行）
- 真 Redis 冒烟（`smoke_rate_limit.py` 用完即删，onehub-redis 容器）：**RATE_LIMIT_SMOKE ALL_PASS**——IO1 满桶连续 5 放行 + 第 6 拒绝 wait=1；IO2 大额 800/1000 二次拒绝 wait=ceil(600/10)=60；IO3 sleep 3.2s 补 token 与 `_refill` 公式对拍（误差 <0.1）；IO4 4 桶 × 28 并发 EVAL 原子性——恰 20 放行零超发；IO5 双维度组合（RPM 第 3 次拒绝）
- 提交链：`08a58fb`（fix test event_loop）→ `bf644ff`（feat rate-limit）→ `b655dcb`（test rate-limit）

**下一步**：W2 任务 4 幂等键（Idempotency-Key，Redis 存储手写，保护清单）——重复请求不重复转发。

## 会话 2026-09-12（W2 任务 2：网关面鉴权中间件表迁移达成，58 passed）

**背景**：任务 1 完成（50 passed）。任务 2 目标 = 鉴权中间件（哈希校验→状态/白名单/过期）全分支单测——require_gateway_api_key 从 .env 固定 key（GATEWAY_API_KEY 配置比对）迁到 api_keys 表。

**做了什么**：

1. TDD 任务 2：
   - RED `test_gateway_auth.py`（10 用例）：有效 active key 200 / 未知明文 401 / revoked 401 / 过期 401 / 无头 401 / 非 Bearer 401 / 白名单纯函数 4 分支 / 端点级白名单拒绝 404（模型全局 enabled 但 key 无权，验证不泄露授权粒度）。首跑 ImportError（check_model_whitelist 不存在）→ RED 成立
   - GREEN 三连：① services/keys.py 增 `check_model_whitelist`（None/空列表全放行，纯同步无 IO）；② security.py 重写 `require_gateway_api_key`（Request+Header 注入 → extract_bearer_token → `hash_sk_key` SHA-256 → `select(ApiKey).where(key_hash==)` → status!=active 401 / expires_at 已过 401，返回 ApiKey 实体，明文永不回读）；③ gateway.py 依赖类型化（返回 ApiKey）+ chat_completions 加白名单授权（`check_model_whitelist` 拒绝 → 404 model_not_found）
2. 桩能力补强：tests/_fake_db.py `_col_and_value` 支持 `is_(True)` 类条件（`Model.enabled.is_(True)` 查询在桩可用）——SQLAlchemy `true()`/`false()` 是模块级单例，`bool()` 求值被禁（`TypeError: Boolean value of this clause is not defined`），改用 `right is true()` 恒等判断；`isnot` 对称处理
3. 死配置清理（迁移收尾）：config.py 删 `gateway_api_key` 字段（已无消费者）；conftest.py 删 GATEWAY_API_KEY 注入；.env.example 注释掉该行并标注「W1 验收脚本用，业务不再读取」；test_security.py 精简（删配置比对用例，保留 Bearer 头解析 4 用例）；test_models_endpoint.py 迁移（预置 `ApiKey(key_hash=SHA256("test-gateway-key"))` 进桩，patch security 与 gateway 两模块 AsyncSession 指向同一 FakeSession）

**验证（dev-verify 证据）**：

- `uv run pytest -q` → **58 passed**（50 + 8 净增，无回归；任务 2 新用例 10 + 精简调整 -2）
- 真机验收（httpx 脚本用完即删，本机 uvicorn :8001）：register/login **200/201**（JWT）→ create key **201**（`sk-d2fc535` 前缀，白名单=[deepseek-v4-flash-0731]）→ GET /v1/models **200**（两模型）→ 白名单内 model 真转发 **200**（content=W2T2-OK，usage=19）→ 白名单外 model **404 model_not_found** → 未知 key **401** → 软删后 key **401**，7 断言 ALL_PASS

**下一步**：W2 任务 3 令牌桶（Redis Lua 手写）+ Semaphore 并发限流——超限 429+Retry-After，dev-tdd 先写测试（保护清单）。

## 会话 2026-09-12（W2 启动：遗留关闭 + 任务 1 管理面 auth+keys CRUD 达成）

**背景**：W1 全量完成（32 passed 基线）。本会话交接遗留 1/2，从 W2 任务 1（租户-用户-Key 三级模型 + 管理端 JWT + SK-Key）开始。

**做了什么**：

1. 遗留 1 关闭：W1 收尾三份独立 Conventional Commits 落地——`408b786`(feat acceptance T025) → `24a18f4`(docs phase8 T026) → `13b4ed7`(docs phase8 T027)，msg 文件已删，工作区 clean。遗留 2（W1 teach 讲解）仍待 Asize 自验（停机点精神，不代答）
2. 停机点 3 处理：写 ADR-0001（docs/adr/0001-w2-jwt-redis-deps.md）提案引入 pyjwt + redis-py → **Asize 批准**（问询两问：ADR 批准 + 任务 1 范围定 min=auth+keys CRUD）→ `uv add redis pyjwt`（pyjwt 2.14.0 / redis 8.1.0）
3. TDD 任务 1（四提交，git log 时间序自证）：
   - RED 安全组件 `ca0d4a8`：test_admin_security.py 覆盖密码哈希（pbkdf2 随机盐）/ JWT 签发校验（过期/篡改→401）/ SK-Key 生成哈希（64 hex 对齐 CHAR(64)）
   - GREEN `69d7c27`：security.py 扩展（hash_password/verify_password pbkdf2、create/decode_access_token pyjwt HS256、require_admin 依赖）+ services/keys.py（generate_sk_key 明文一次/prefix/hash）+ conftest SECRET_KEY 提到 32+ 字节（消 HS256 KeyLengthWarning）+ .env.example 注释提醒
   - deps `12954ae`：pyproject+uv.lock 落 redis/pyjwt
   - RED 端点 `8cda949`：tests/_fake_db.py（通用离线桩：add/commit/refresh/scalars/scalar/get + whereclause 解析，跨请求共享单例工厂）+ test_admin_auth（6 用例）+ test_admin_keys（5 用例）
   - GREEN `8ceb3e6`：app/api/admin/{auth,keys}.py + schemas/admin.py + ConflictError(409) + main.py 注册管理面路由；SQLAlchemy 2.0 桩适配（raw column 为 AnnotatedTable 反查 __tablename__、右值 BindParameter 取 .value）；create key 显式 status="active" 不依赖 DB server_default
4. 真机验收（dev-verify 证据，httpx 脚本用完即删，本机 uvicorn :8001 + 本地 pg）：
   - register **201**（JWT 160 字符）→ 重复 register **409 conflict_error** → login **200**（user 回显）→ 错密码 **401 authentication_error** → keys 无 JWT **401** → create key **201**（`sk-832ad15` 前缀 + 白名单落库）→ list keys **200**（脱敏，无 key_hash/key）→ delete **204** → 软删后 status=**revoked**

**验证（dev-verify 证据）**：

- `uv run pytest -q` → **50 passed**（32 基线 + 18 新增，无回归）
- 上述真机 9 项断言全 PASS（命令输出如上）
- git log：`... → 12954ae(deps) → ca0d4a8(test RED) → 69d7c27(feat GREEN) → 8cda949(test RED 端点) → 8ceb3e6(feat GREEN 端点)` TDD 时间序完整

**下一步**：W2 任务 2 鉴权中间件（网关面 Key 表哈希校验 → 状态/白名单/过期，全分支单测）——require_gateway_api_key 从 .env 固定 key 迁到 api_keys 表。

## 会话 2026-09-12（Phase 8：T025–T027 + SC-001/002/003/004/005 全项终判，W1 收尾）

**背景**：Phase 7 完成（US5 一键环境 SC-003 达成），Docker 三容器健康（onehub-api/pg/redis），.env 含真实 SenseAudio 渠道 key。从 T025 进入 W1 收尾（Polish & Cross-Cutting Concerns）。

**做了什么**：

1. T025 JS SDK 验收脚本：`scripts/acceptance/package.json` + `scripts/acceptance/verify.mjs`（openai **^7.15.0**，`run verify` 五断言任一失败 exit 1）。实施要点：
   - 配置隔离：脚本用独立 `GATEWAY_BASE_URL`（默认 `http://localhost:8000/v1`），**读根 .env 时只提取 `GATEWAY_API_KEY`**——首轮运行全 404 的根因是 .env 的 `BASE_URL`（渠道上游键名，Phase 3 键名决策）混入 `process.env`，SDK 去打 SenseAudio 而非本地网关；修复后五断言全过
   - 模型 ID 用种子实际 ID `deepseek-v4-flash-0731` / `senseaudio-s2`（quickstart 断言 4 的 `deepseek-chat` 是旧 ID，不照抄）
   - Windows/libuv 崩溃修复：收尾 `await client.close()` + `process.exitCode` 赋值，替代 `process.exit()`（后者直接杀 keep-alive 句柄触发 libuv 断言崩溃，实测 exit 3221226505）
   - 流式断言不显式传 `stream_options.include_usage`——顺带验证「调用方零配置」拿到 usage（网关侧注入由 T011 `_build_payload` 保证）
2. T026 全链路验收：Docker compose 环境（三容器健康）+ `npm run verify` 五断言全过 + SC-004 错误抽查（临时 httpx 脚本防 GBK，验证后已删）三分支 PASS
3. T027 W1 收尾文档：本日志 + task_plan.md 任务 0-4 状态与证据 + notes.md 周学习笔记（首次创建，teach 检查点材料同源）

**验证（dev-verify 证据）**：

- `cd scripts/acceptance && npm run verify` → **5/5 [PASS]，exit 0**：
  1. 非流式：`choices[0].message.content` 非空 + `usage.prompt_tokens > 0`（SC-001）
     2+3. 流式：delta 拼接完整（含 ACC-PASS-OK）+ 正常收尾（finish_reason=stop，[DONE] 由网关收尾）；末尾 chunk usage **prompt/completion/total 全非零正整数**（SC-002，调用方零配置）
  2. models list `data[]` 含两实际 ID（deepseek-v4-flash-0731、senseaudio-s2）（US4）
  3. 错 key → 401 + `error.message` 存在（SC-004）
- SC-004 错误抽查（httpx，防 PowerShell GBK）三分支全 PASS：
  - 未知 model（带 key）→ **404**，OpenAI 结构 `type=invalid_request_error` + `code=model_not_found`（对齐官方语义）
  - 无 key / 错 key → **401** `type=authentication_error`（`code=null` 为官方认证错误常规形态，T009 既定实现）
- `uv run pytest -q` → **32 passed**（基线保持，无回归）
- SC 终判：SC-001 ✅（SDK 仅改 base_url 流式+非流式全成功）｜SC-002 ✅（五断言 3 流式末尾 usage 全字段非零；Phase 5 真机曾校验 usage=14/5/19 total=sum）｜SC-003 ✅（Phase 7 达成，本会话复核三容器健康、8 端口监听）｜SC-004 ✅（错误抽查 3 分支 + T014 五类上游错误映射单测 + T008 422/500 出口单测全绿，无堆栈泄漏）｜SC-005 ✅（`_stream_events` 逐事件 yield 不缓冲整段：StreamingResponse + `Cache-Control: no-cache` + `X-Accel-Buffering: no`，首块即时下发；Phase 4 真机 12 事件逐块透传佐证）

**下一步**：W2（多租户鉴权 + 限流，task_plan.md 阶段总览推进）；W1 teach 检查点讲解材料已备（notes.md），**待 Asize 自验讲解通过**（不代答，停机点精神）。

## 会话 2026-09-12（Phase 7：T023–T024 + SC-003 达成，一键环境可演示）

**背景**：Phase 6 完成（US4 模型列表，32 passed），Docker 三容器健康，.env 含真实 SenseAudio 渠道 key。从 T023 进入 US5 一键环境（P3）。

**做了什么**：

1. 关键前置发现：原镜像（build context=backend/）只含 app/，无 alembic.ini/alembic//scripts/seed.py——启动 3 个必需文件都不在镜像里；且 context 限于 backend/ 够不到仓库根 scripts/seed.py。故 T023 实施「路线 B 变体」：
   - 新建根 `.dockerignore`（挡 `.env` 真实 key/`.git`/`.venv`/`node_modules` 等，扩 context 后密钥红线与体积双保障；构建 context 255KB）
   - `docker-compose.yml`：api `build` 改 `context: .` + `dockerfile: backend/Dockerfile`；`command` 改 shell 串 `alembic upgrade head && PYTHONPATH=/app python scripts/seed.py && exec uvicorn`
   - `backend/Dockerfile`：COPY 路径加 backend/ 前缀 + 实际 COPY alembic.ini / alembic / scripts/seed.py（镜像自包含）；CMD 保持 uvicorn（含 uv run，compose 已覆盖）
   - seed.py 零改动：容器内 project root=/app，其 `parents[1]+/backend` 相对定位失效但 sys.path 插入不存在路径无害，PYTHONPATH=/app 接管 import app
2. T024 双场景验收（dev-verify 证据留证）：
   - 单容器重建（`up -d --build api` 不动 pg）：日志 `[seed] ensured channel=deepseek-main models=['deepseek-v4-flash-0731','senseaudio-s2']`（幂等，无 Running upgrade 输出=已在 head）+ uvicorn 8000 → /docs 200
   - 全链路空库（`down -v` 清卷 → `up -d --build`）：api 日志 **`Running upgrade -> d36075ae03bd, T006 initial 7 tables`** + seed 插入 → /docs 200；重复 `up -d` 三容器 Running（零重建、种子零重复执行，幂等成立）→ GET /v1/models 200（object=list，data[]=deepseek-v4-flash-0731/senseaudio-s2，created=0/owned_by=onehub）

**验证（dev-verify 证据）**：

- 上述命令输出：`docker compose up -d --build api` 重建成功（依赖层缓存命中）→ logs 见 seed 幂等 + /docs **200**
- `docker compose down -v && up -d --build` → logs 见 `Running upgrade -> d36075ae03bd` + `[seed] ensured ...` + /docs **200**；重复 `up` 零操作；GET /v1/models **200** 两模型 ID
- `uv run pytest -q` → **32 passed**（基线保持，conftest 指向 onehub_test 库不受 down -v 影响）
- git log：`7ac8fdc`(feat docker T023) 接续 Phase 6 `df74365`
- 决策落盘：task_plan.md 关键决策记录 +1（Phase 7 执行期设计：扩 context + .dockerignore + command shell 串，seed 零改动）

**下一步**：Phase 8 T025（JS SDK 验收脚本 verify.mjs 五断言，P 可与 Phase 5 后独立写）→ T026（全链路验收 SC-001/002/004/005 终判）→ T027（W1 收尾文档 + teach 检查点）。

## 会话 2026-09-12（Phase 6：T021–T022 + US4 达成，模型列表可演示）

**背景**：Phase 5 完成（US3 流式 usage，30 passed），Docker 三容器健康，.env 含真实 SenseAudio 渠道 key。从 T021 进入 US4 模型列表（仅依赖 Phase 2）。

**做了什么**：

1. T022（RED）测试先行：`tests/unit/test_models_endpoint.py`——设计决策点：DB 来源取「离线桩 AsyncSession」（monkeypatch gateway 模块 AsyncSession，假会话 async with + scalars() 模拟 DB 层 enabled 过滤，TestClient 走真实 app + lifespan 惰性建连，零 Docker 依赖、离线可复跑，与 T014 MockTransport 的 mock 外部依赖风格一致），数据用种子形态（deepseek-v4-flash-0731 / senseaudio-s2 两 enabled + disabled-model）。两用例：① list 结构 + 只含 enabled（含 created=0/owned_by="onehub" 壳层断言，对齐 T021）；② 无 key 401 authentication_error。运行确认 404 FAIL 后单独提交 `8e158a0`
2. T021 实现转 GREEN：`gateway.py` 增 `GET /v1/models`——models 表 `enabled.is_(True)` 集合 → OpenAI list 格式（object=list，data[] 每项 id=model_name/object="model"/created=0/owned_by="onehub"，后两者协议壳层静态默认值），挂 `require_gateway_api_key`（contracts 全端点多面鉴权）。2 passed，提交 `34095c4`
3. 全量回归：`uv run pytest -q` → **32 passed**（30 存量 + 2 新增）

**验证（dev-verify 证据）**：

- `uv run pytest -q` → 32 passed
- git log TDD 时间序：`8e158a0`(test RED) → `34095c4`(feat GREEN)
- 真机验收（临时 httpx 脚本模拟 SDK 改 base_url 打本机 uvicorn :8001，DATABASE_URL 覆盖 localhost，脚本已删）：**GET /v1/models 200**，object=list，`data[].id = ['deepseek-v4-flash-0731', 'senseaudio-s2']`（含启用模型、无旧 deepseek-chat）；无 key → **401** `{"error":{"type":"authentication_error"}}` → **ACCEPT US4**
- 决策落盘：task_plan.md 关键决策记录 +1（Phase 6 执行期设计）

**下一步**：Phase 7 T023–T024（US5 一键环境：api 容器 entrypoint 串 migration+seed）+ W1 teach 检查点。

## 会话 2026-09-12（Phase 5：T018–T020 + SC-002 达成，US3 流式 usage 可用）

**背景**：Phase 4 完成（US2 流式 SSE 透传 12 事件 + [DONE]），Docker 三容器健康，.env 含真实 SenseAudio 渠道 key。从 T018 进入 US3 流式 usage（保护清单，测试先行）。

**做了什么**：

1. T018（RED）测试先行：`tests/unit/test_usage_extract.py` 五项覆盖——末尾 chunk usage 提取 / delta 累计口径（透传不破坏）/ 上游未返 usage 缺省 0 不报错 / EOF 截断合成 0 兜底 / with_usage 异步适配合成事件位次。运行确认 `ImportError: UsageTracker` FAIL 后单独提交 `70563bc`
2. T019 实现转 GREEN：`forward.py` 手写 usage 提取（保护清单，禁换库）——`UsageTracker` 同步核心（feed 原样透传 + 提取 usage，事件存样例 id/created/model；finish() 未提取到则合成 usage=0 兜底 chunk，choices=[]，位次=流尾最后一个，gateway 的 [DONE] 前）+ `with_usage()` 异步适配包 `sse_events`；`base.py` chat_stream 由 `sse_events(...)` 换 `with_usage(sse_events(...))`。include_usage 注入已在 T011 `_build_payload` 就位（stream=true 无条件注入），未重写。5 passed，提交 `78c68a4`
3. T020 端到端：`tests/test_e2e_stream.py`——MockTransport 回放两条录制 SSE 流（带 usage / 不带 usage）打 DeepSeekProvider.chat_stream 全级联（真实 httpx stream 协议层 + SSELineParser + UsageTracker）。断言：载荷注入 `stream_options.include_usage=true`（T019 注入端）✅ / 上游带 usage → 仅末尾 chunk usage 非零 12/5/17 且 delta 累计完整（SC-002）✅ / 上游不带 usage → 末尾合成 0/0/0 兜底不报错、delta 原位透传 ✅。3 passed，提交 `941c570`
4. 调试复盘：① 单测 `test_delta_accumulation_untouched` 自身越界（合成兜底事件 choices=[] 未防御），修测试语义（OpenAI usage chunk 无 delta）非实现问题；② e2e 断言忘算 role 块（content=None）；③ e2e 补 `p.aclose()` 释放连接池消 pending task 警告

**验证（dev-verify 证据）**：

- `uv run pytest -q` → **30 passed**（22 存量 + 5 usage 提取 + 3 e2e）
- git log TDD 时间序：`70563bc`(test RED) → `78c68a4`(feat GREEN) → `941c570`(e2e)
- 真 key 流式验收（临时 httpx 脚本模拟 SDK 改 base_url 打本机 :8001，脚本已删）：**HTTP 200 text/event-stream**、3 个 data 事件、`[DONE]` 收尾、**末尾 chunk usage {prompt_tokens: 14, completion_tokens: 5, total_tokens: 19} 全非零且 total=14+5**、delta 拼接完整=USAGE-OK → **ACCEPT SC-002**（SenseAudio 真实支持 include_usage，上游 usage 原样透传；合成 0 兜底路径由 T018/T020 自动化覆盖）

**下一步**：Phase 6 T021–T022（US4 GET /v1/models 模型列表，仅依赖 Phase 2）+ W1 teach 检查点。

## 会话 2026-09-12（Phase 4：T015–T017 + CP4.1 达成，US2 流式 SSE 透传可演示）

**背景**：Phase 3 完成（US1 非流式 MVP 200），Docker 三容器健康，.env 含真实 SenseAudio 渠道 key。从 T015 进入 US2 流式（保护清单，测试先行）。

**做了什么**：

1. T015（RED）测试先行：`tests/unit/test_sse_parser.py` + `tests/fixtures/sse_stream.txt`——五能力全覆盖：data: 前缀剥离 / 空行分隔 / `[DONE]` 终止（后续行不产出）/ 坏 JSON 事件跳过（前后事件不受影响）/ EOF 截断自然收敛；另含多 data: 行拼接与注释/空白行忽略。运行确认 `ModuleNotFoundError: app.services` FAIL 后单独提交 `d77cb29`
2. T016 实现转 GREEN：`app/services/forward.py` 手写 SSE 解析器（保护清单，禁换库）——`SSELineParser` 同步状态机（feed 增量喂行、空行结算事件、`[DONE]` done、坏 JSON 跳过、finish() EOF 兜底）+ `sse_events()` 异步适配（httpx aiter_lines 直喂）；`base.py` chat_stream() 实现：`client.stream()` + 状态码检查（复用 `_map_upstream_error`）+ 逐事件产出，超时/连接失败映射 504/502。5 passed，全量 22 passed，提交 `ec094bd`
3. T017 端点：`gateway.py` 增 `stream: true` 分支——`_stream_events()` 事件 dict → `data: {json}\n\n` + `data: [DONE]` 收尾，`StreamingResponse(media_type="text/event-stream")` + `Cache-Control: no-cache` + `X-Accel-Buffering: no`；调用方断连由生成器取消传播终止上游（async with 退出关闭连接）。提交 `6307935`
4. 验收（dev-verify 证据）：临时 httpx 脚本模拟 SDK 改 base_url 打本机网关 → 上游真实渠道

**验证（dev-verify 证据）**：

- `uv run pytest -q` → **22 passed**（17 存量 + 5 新增 SSE 解析器）
- 真 key 流式转发（本机 uvicorn :8001，DATABASE_URL 覆盖 localhost）→ `HTTP 200 text/event-stream`、**12 个 data 事件逐块透传**、delta 拼接完整 = "我是 CP4-OK，一个专注高效解决问题的智能助手，随时准备为你提供简洁、准确的帮助。"、`data: [DONE]` 正常收尾 ✅
- `git log` TDD 时间序：`d77cb29`(test RED) → `ec094bd`(feat GREEN) → `6307935`(端点)

**下一步**：Phase 5 T018–T020（US3 流式 usage：末尾 chunk 携带 token 数，include_usage 注入已在 `_build_payload` 就位，T018 先写 usage 提取测试先 RED）。

## 会话 2026-09-12（Phase 3：T010–T014 + CP3.1 达成，US1 非流式 MVP 可演示）

**背景**：Phase 3 前置就绪（config 双键名 + env_file 绝对路径，上一会话）。Docker 三容器运行中，.env 含真实渠道 key。

**做了什么**：

1. T010 schemas：`app/schemas/chat.py`（ChatCompletionRequest 含透传 + extra=allow、Message、ChatCompletionResponse/Choice/Usage、流式 ChatCompletionChunk/Delta/ChunkChoice）严格对齐 contracts
2. T011+T012 providers：`base.py` BaseProvider ABC（`_headers()` 抽象；chat() 通用实现——AsyncClient 连接池（limits 100/20）+ `_normalize_base_url()` 自动补 /v1、`_build_payload()`（exclude_none + 非流式不注入）、五类错误映射、响应结构校验零堆栈；chat_stream 占位留 Phase 4）+ `deepseek.py`（仅 Bearer 头差异点）
3. T013 gateway：`app/api/v1/gateway.py` POST /v1/chat/completions（require_gateway_api_key → models 表 enabled 校验 404 model_not_found → app.state 单例 provider → provider.chat()）；main.py lifespan 构造 provider 单例 + router 注册
4. T014 单测：`tests/unit/test_error_mapping.py`（401→authentication_error / 429→rate_limit_error / 502→api_error / 超时→504 / 连接拒绝→502 + 200 结构非法兜底 + 2xx 回归对照；MockTransport 注入，message 防泄漏断言）
5. Asize 决策落地：base_url 自动规整 /v1 + 种子模型更新（deepseek-chat/reasoner → deepseek-v4-flash-0731/senseaudio-s2，SenseAudio 平台实际可用），dev 库数据同步

**验证（dev-verify 证据）**：

- `uv run pytest -q` → **17 passed**（10 存量 + 7 新增错误映射）
- 本机 uvicorn（localhost:8001，DATABASE_URL 覆盖 localhost）三分支：401 authentication_error ✅ / 404 model_not_found ✅ / 真 key 非流式转发 **200**（object=chat.completion，content=CP31-OK，usage 12/5/17，finish=stop）✅
- 真机诊断链：getaddrinfo 失败（.env 主机名 pg）→ 502（上游 404：中转只认 /v1）→ 400 模型未找到 → 中转 /v1/models 列模型 → 决策落地后 200

**下一步**：Phase 4 T015–T017（US2 流式 SSE 透传，保护清单 T015 先写测试先 RED）。

## 会话 2026-09-12（Phase 2：T005–T009 + Checkpoint 达成，阻塞解除）

**背景**：Docker 三容器运行中，.env 已建，从 T005 继续 Phase 2（不依赖真实 key）。

**做了什么**：

1. T005 SQLAlchemy 7 表：`app/models/`（tenant/user/api_key/channel/model/usage_record/balance + `__init__.py` 定义 Base 聚合导出），严格按 PROJECT_CONTEXT 第 5 节 DDL（BIGSERIAL/CHAR(64) key_hash/ARRAY(Text) whitelist/UUID request_id 唯一 + idx_usage_key_time 复合索引）
2. T006 Alembic：`alembic.ini` + `alembic/env.py`（async 引擎，URL 由 Settings 注入：环境变量优先、本地回退 localhost）+ autogenerate 生成 `001_initial`（7 表 + 索引一次建齐，只增不改）→ `alembic upgrade head` 对 pg 执行成功
3. T007 种子：`scripts/seed.py`（deepseek-main 渠道 `api_key_encrypted=env-injected` + deepseek-chat/deepseek-reasoner 两模型挂渠道；查重式幂等 upsert，sys.path 注入 backend）
4. T008 错误出口：`app/core/errors.py`（OpenAI 错误结构 `{"error":{message,type,param,code}}`；OpenAIError/AuthenticationError/ModelNotFoundError/RateLimitError/UpstreamError；422→invalid_request_error、未捕获→api_error 零堆栈、401 带 WWW-Authenticate）→ 注册进 create_app
5. T009 鉴权：`app/core/security.py`（extract_bearer_token + hmac.compare_digest constant-time 比对）+ `tests/unit/test_security.py` 四分支（有效/缺失/值错误/scheme 格式错）
6. 补错误出口冒烟单测 `tests/unit/test_errors.py`（422/404 model_not_found/500 三类出口结构断言）

**验证（dev-verify 证据）**：

- `alembic upgrade head` → `Running upgrade -> d36075ae03bd`；pg `\dt` 7 表 + `idx_usage_key_time` 全在
- `uv run python ../scripts/seed.py` 连跑两遍 → channels 1 行 / models 2 行唯一，外键挂接正确
- `uv run pytest -q` → **10 passed**（冒烟 1 + 鉴权 6 + 错误出口 3）
- `uv run python -c "from app.main import app"` → OpenAIError/RequestValidationError/Exception handlers 已注册
- `docker compose up -d --build api` 重建 → `GET /docs` → **200**

**下一步**：Phase 3 T010–T014（US1 非流式 MVP：schemas → Provider 模板方法基类 → DeepSeek 实现 → POST /v1/chat/completions + 错误映射单测）。Phase 3 起需真实 `DEEPSEEK_API_KEY`（.env 填入）。

## 会话 2026-09-12（Phase 1：T001–T004 + Checkpoint 达成，停机点 2 解除）

**背景**：Asize 确认 plan/tasks，从 T001 开始执行 Phase 1。

**做了什么**：

1. T001 pyproject.toml：fastapi/uvicorn/sqlalchemy[asyncio]/alembic/asyncpg/httpx/pydantic-settings + pytest/pytest-asyncio dev 组；uv lock 生成 uv.lock（37 包）入库；uv sync 落 backend/.venv（Python 3.12.3）
2. T002 FastAPI 骨架：`app/main.py`（create_app 应用工厂 + lifespan 管理 AsyncEngine 池，pool_pre_ping）+ `app/core/config.py`（pydantic-settings 五变量，默认值与 .env.example 对齐）
3. T003 容器编排：`docker-compose.yml`（api/pg/redis 三服务 + healthcheck + 依赖等待）+ `backend/Dockerfile`（python:3.12-slim + uv 0.12.7 二进制，uv sync --frozen --no-dev 层缓存）+ `.env.example`（五变量含注释，密钥红线不入 git）
4. T004 pytest 基建：`tests/conftest.py`（env 覆盖 + session event loop）+ 冒烟测试；pyproject 增 `asyncio_default_fixture_loop_scope`
5. Checkpoint：起 Docker Desktop → 创建 .env（复制模板）→ `docker compose up -d --build` → 探活

**验证（dev-verify 证据）**：

- `uv run pytest -q` → `1 passed in 0.07s`
- `uv run python -c "from app.main import app"` → 路由含 `/docs` `/openapi.json`
- `docker compose up -d --build` → 三服务 Up，pg/redis healthy
- `Invoke-WebRequest http://localhost:8000/docs` → **HTTP 200，`OneHub Gateway - Swagger UI`**
- 提交链：`a66bd06`（T001）→ `0bcc421`（T002）→ `7c496bc`（T003）→ `772a439`（T004），各自独立 Conventional Commits

**下一步**：Phase 2 T005–T009（7 表 SQLAlchemy + Alembic 全量迁移 + seed 幂等 + OpenAI 错误出口 + Bearer 鉴权单测），Checkpoint = 迁移可对 pg 执行、种子幂等、错误与鉴权单测绿。

## 会话 2026-09-12（git 仓库初始化，跨会话前保障）

**做了什么**：

1. 根 `.gitignore`：挡 `.env`（密钥红线）、Python（`__pycache__`/`.venv`/`.pytest_cache`）、Node（`node_modules`）、Docker 本地数据卷（`postgres_data/` 等）、IDE/OS 杂物；显式注明 uv.lock 与 alembic 迁移必须入库
2. `git init`（主分支 main）+ 全量暂存 + 首次提交
3. `.specify/feature.json` 被 spec-kit 自带 `.gitignore` 正确忽略（本地指针，按其约定不入库）

**验证（dev-verify 证据）**：

- `git log --oneline` → `fd694d9 chore: 初始化仓库——spec-kit 脚手架、W1 规格五件套、AGENTS.md 与进度三件套`（44 文件，5933 行）
- `git status` → `nothing to commit, working tree clean`
- 暂存清单人工核查：零 `.env` 类文件命中（密钥红线守卫成立）

**意义**：TDD 证据链就位——后续 T015/T018（保护清单测试先行）可通过 git log 时间序自证。

## 会话 2026-09-11（W1 任务 0 收尾：clarify 回填 + plan + tasks）

**做了什么**：

1. Asize 确认全部按推荐回答 /clarify 五问（Q1:B / Q2:A / Q3:B / Q4:A / Q5:B）
2. 回填 spec：全部 [NEEDS CLARIFICATION] 清除，增 Clarifications 节（5 条 Q→A 记录）；checklist 16/16 通过
3. /speckit-plan 产出五件套（specs/001-openai-compat-gateway/）：
   - plan.md（技术上下文/Constitution Check 5 项全过/W1 源码结构）
   - research.md（D1 Provider 接口两版对比→候选 B 模板方法基类；D2 密钥 env 注入+占位；D3 SSE 要点；D4 错误映射；D5 compose 分期）
   - data-model.md（7 表全量、W1 只消费 channels/models，种子规格）
   - contracts/openai-compat-api.md（两端点 + 错误契约 + include_usage 注入规则）
   - quickstart.md（SC-001~005 验收手册，≤2 命令起环境 + JS SDK 五断言）
4. /speckit-tasks 产出 tasks.md：T001–T027，八阶段（Setup → Foundational → US1 非流式 → US2 SSE[TDD] → US3 usage[TDD] → US4 models → US5 环境 → Polish 验收）；保护清单 T015/T018 强制测试先行
5. 三件套同步：决策记录 +3 条，任务 0 状态更新

**测试/验证**：

- 无代码。质量证据：spec checklist 16/16；Constitution Check 五原则全过（plan.md）；任务格式全部含 checkbox/ID/文件路径

**停机点 2（当前）**：plan/tasks 已产出，**等 Asize 确认后才写代码**。确认后从 T001（pyproject 初始化）开始执行 Phase 1。

**下一步（确认后）**：

1. Phase 1 T001–T004：骨架 + compose + pytest 基建 → checkpoint（/docs 200）
2. Phase 2 T005–T009：7 表 + 迁移 + 种子 + 错误出口 + 鉴权
3. 后续按 tasks.md 依赖序推进，MVP = US1（非流式）

## 会话 2026-09-10（W1 任务 0，spec-kit 启动）

**做了什么**：

1. `specify init . --integration claude`（v1.0.1，ps 脚本）→ `.specify/` + `.claude/skills/speckit-*` 就位
2. 填充 `.specify/memory/constitution.md`：五条核心原则（协议兼容硬标准 / 最小改动 / TDD+保护清单 / 证据先行 / 停机点），源自 PROJECT_CONTEXT 第 2/8 节
3. /speckit-specify 产出 `specs/001-openai-compat-gateway/spec.md`：5 个 User Story（P1×3：非流式/流式/usage；P2×2：models/错误映射；P3：一键环境）+ FR-001~011 + 成功标准 + 4 个 [NEEDS CLARIFICATION]
4. 质量清单 `specs/001-openai-compat-gateway/checklists/requirements.md`：除"NEEDS CLARIFICATION 清零"外全部通过
5. `.specify/feature.json` 写入活跃 feature 目录
6. AGENTS.md 生成（writing-for-agents 原则：不复制 PROJECT_CONTEXT，只做指针 + 保护清单/停机点/禁改清单/工作流）
7. 三件套建立：docs/task_plan.md（W1–W4 全计划）、docs/findings.md、docs/progress.md（本文件）
8. /speckit-clarify 扫描完成，5 问已列给 Asize——**停机等待亲自回答**

**测试/验证**：

- 无代码，无测试。spec 自检：checklists/requirements.md 15/16 通过，唯一未过项 = NEEDS CLARIFICATION 未清零（预期内，等回答）

**下一步**（等 Asize 回答 Q1–Q5 后）：

1. 回填 spec（去 NEEDS CLARIFICATION 标记）+ 更新 checklist
2. /speckit-plan（技术方案）→ /speckit-tasks（任务清单）
3. 进入 W1 任务 1：脚手架（compose 起 pg/redis + FastAPI，验收 /docs 可访问）

**未决事项**：

- /clarify Q1–Q5 等 Asize 亲自回答（停机点 1，禁止代答）
- notes.md（周记）与 docs/adr/ 在首个使用场景出现时创建
