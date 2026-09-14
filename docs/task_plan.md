# OneHub 任务计划（W1–W4）

> 本文件是项目唯一进度真相源。只按实际进度更新，不虚构完成状态（禁改清单）。
> 每次会话开始先读本文件同步进度，再动手（AGENTS.md 会话启动规则）。

## 目标

交付 OpenAI 协议兼容的多模型 LLM API 聚合网关：鉴权、限流、计费、高可用四类工程问题各就各位，Locust 压测数字进简历。

## 当前状态

**W1 · Phase 1–8（T001–T027）完成**：Asize 已确认 plan/tasks（停机点 2 解除）。Python 3.12 项目初始化（uv.lock 入库）、FastAPI 骨架（应用工厂 + lifespan + config）、compose（api/pg/redis）三容器构建健康、pytest 基建就绪。Phase 2：7 表 SQLAlchemy 模型 + Alembic async 迁移 + 种子脚本（幂等）+ OpenAI 统一错误出口 + Bearer 鉴权（全分支单测）。Phase 3（US1 非流式 MVP）：schemas 协议模型 → BaseProvider 模板方法基类（连接池 + 错误映射）→ DeepSeekProvider → POST /v1/chat/completions（鉴权→enabled 校验→渠道透传）+ 错误映射五类单测。Phase 4（US2 流式 SSE 透传，保护清单 TDD）：T015 SSE 解析器测试先行（RED）→ T016 forward.py（SSELineParser 状态机 + sse_events 异步适配 + provider.chat_stream）→ T017 gateway stream:true 分支（StreamingResponse 直通），T015 转 GREEN。Phase 5（US3 流式 usage，保护清单 TDD）：T018 usage 提取测试先行（RED）→ T019 forward.py 增 UsageTracker（透传+提取，上游未返 usage 合成 0 兜底）+ with_usage 适配 + chat_stream 接入（T018 转 GREEN）→ T020 Mock 上游 SSE 端到端。Phase 6（US4 模型列表）：T022 端点单测先行（离线桩 AsyncSession，RED 404）→ T021 GET /v1/models（enabled 集合 → OpenAI list，挂鉴权依赖），32 passed。Phase 7（US5 一键环境，SC-003）：T023 build context 扩根 + .dockerignore（挡 .env 红线）+ 镜像自包含（实际 COPY alembic/seed.py）+ compose command shell 串「迁移 → 幂等种子 → exec uvicorn」；T024 空库 down -v 一键重建 + 重复 up 幂等验证，32 passed 保持。**CP3.1 证据**：本机 uvicorn 真 key 非流式转发 200（`chat.completion` 结构，content=CP31-OK，usage 12/5/17）；401/404 异常分支 OpenAI 结构正确。**CP4.1 证据**：真 key 流式转发 200（`text/event-stream`，12 个 data 事件逐块透传，delta 拼接完整=CP4-OK 整句，`data: [DONE]` 正常收尾）。**CP5.1 证据**：真 key 流式末尾 chunk usage 非零（prompt 14 / completion 5 / total 19，SenseAudio 支持 include_usage，上游 usage 原样透传；若上游不返则有合成 0 兜底，SC-002）。**US4 证据**：GET /v1/models 真机 200（object=list，data[]=['deepseek-v4-flash-0731','senseaudio-s2']，无旧 ID）+ 无 key 401。**US5/SC-003 证据**：`docker compose up -d --build` 空库一键启动（api 日志 `Running upgrade -> d36075ae03bd` + `[seed] ensured channel=deepseek-main models=[...]`）→ /docs 200 → 重复 `up` 无任何重建/重复种子（幂等）→ GET /v1/models 200 两模型 ID；重建 api 不动 pg 场景同样通过。**Phase 8（T025–T027，W1 收尾）完成**：T025 `scripts/acceptance/`（openai SDK 按下文 `npm run verify` 五断言，任败 exit 1）；T026 全链路验收终判 SC-001/002/004/005 全过（SC-003 已于 Phase 7 达成）；T027 收尾文档（本文件 + progress.md + notes.md 周笔记，teach 检查点材料同源，讲解待 Asize 自验）。**下一步 W2 多租户鉴权 + 限流**（任务按下方阶段总览推进）。

## 阶段总览

### W1 协议兼容层 + 单渠道转发（2026-09-10 起）

| # | 任务 | 状态 | 验收标准 |
|---|------|------|---------|
| 0 | spec-kit 流程 + AGENTS.md + 三件套 | completed | spec/plan/tasks 齐 + 本三件套就位 |
| 1 | 脚手架：FastAPI + SQLAlchemy + Alembic + compose（pg/redis） | completed | `docker compose up` 后 /docs 可访问 ✅；Alembic 接入属 T006（Phase 2）✅ |
| 2 | OpenAI 兼容端点 + DeepSeek Provider（两版接口候选对比后定稿） | completed | OpenAI SDK 改 base_url，流式/非流式均能对话：非流式 CP3.1（200 + usage）✅、流式 CP4.1（text/event-stream 逐块 + [DONE]）✅；models 列表 US4：GET /v1/models 200 object=list + 无 key 401 ✅（T022 决策：测试用离线桩，见决策记录）｜**Phase 8 终验**：npm run verify 五断言 5/5 exit 0（T025/T026）✅ |
| 3 | SSE 流式透传 + delta 累计 usage | completed | 流式结束 usage 事件有 token 数：T018 先写测试（Phase 5）✅ CP5.1 真 key 末尾 usage 14/5/19 非零（SC-002）✅；上游不返 usage 合成 0 兜底不报错（T018/T020 覆盖）✅ |
| 4 | teach 检查点：FastAPI async、Pydantic/OpenAPI、适配器模式、httpx、SSE | pending（材料已备 T027） | 讲解通过（由 Asize 自验，不代答） |

### W2 多租户鉴权 + 限流

| # | 任务 | 状态 | 验收标准 |
|---|------|------|---------|
| 1 | 租户-用户-Key 三级模型 + 管理端 JWT；SK- Key 生成/哈希/白名单 | completed | 模型与 CRUD 可用：管理面 register/login（JWT HS256 签发）+ keys CRUD（sk- 生成/SHA-256 哈希入库/白名单/软删）+ 租户隔离 ✅ |
| 2 | 鉴权中间件（哈希校验→状态/白名单/过期） | completed | 全分支单测 ✅：表鉴权（hash_sk_key SHA-256 → key_hash 查表 → status/expires_at）六分支 + 白名单纯函数四分支 + 端点 404（58 passed，真机 7 断言 ALL_PASS）|
| 3 | 令牌桶（Lua）+ Semaphore（dev-tdd 先写测试） | completed | 超限 429+Retry-After，并发无竞态 ✅：手写 Redis Lua 令牌桶（RPM 请求 1 / TPM 估算 token，双维度短路合并）+ 进程内并发槽（try_acquire/release 配对，流式 finally 释放）→ 桶拒/槽满均 429 + Retry-After 头（79 passed，真 Redis 冒烟 5 项 IO ALL_PASS：满桶放行/Retry-After 计算/补 token 公式对拍/28 并发 4 桶恰 20 放行零超发/双维度组合） |
| 4 | 幂等键支持 | completed | 重复请求不重复转发 ✅：非流式请求带 Idempotency-Key → Redis Lua 原子占位防并发双转发（手写，保护清单）→ 响应缓存回放（TTL 24h）→ 得主失败撤销可重试 / 超时 409；92 passed + 真 Redis 冒烟 5 项 IO ALL_PASS（10 并发同 key 恰 1 得主其余回放） |
| 5 | teach 检查点：API Key 体系、限流四算法、Redis 原子性/Lua、幂等 | pending（材料已产出） | 讲解通过（Asize 已拍板：全部 teach 检查点延后至项目完成集中自验；W2 五讲材料已入 docs/notes.md） |
| 6 | 📌 简历上墙点 1 + security-best-practices 审查 | completed | 简历初版（docs/resume_draft.md）+ 审查报告（docs/security_review_w2.md）✅ |

### W3 计费引擎

| # | 任务 | 状态 | 验收标准 |
|---|------|------|---------|
| 1 | models 定价表 CRUD；余额预检（Redis） | done | 预检不查库、不足返 402 |
| 2 | 用量事件 → Redis Stream → Celery 异步落库 | done | 链路通畅 ✅（网关 XADD → worker 消费 → 幂等落库零重复；116 passed + 真 Redis+PG 冒烟 11 事件→10 行零重复） |
| 3 | 并发扣减：乐观锁 + 失败重试 | done | 对账脚本通过（PROJECT_CONTEXT 6.3 标准）✅：50 事件幂等落库零重复 + 余额==初始−Σcost 精确一致 + 50 路并发扣减收敛（126 passed） |
| 4 | teach 检查点：幂等、事务隔离、并发扣减三方案、削峰 | pending | 讲解通过 |

### W4 智能路由 + 管理台 + 压测

| # | 任务 | 状态 | 验收标准 |
|---|------|------|---------|
| 1 | 加权轮询 + 指数退避重试（含抖动）；三态熔断器 | done | 封主渠道自动切备、恢复切回 ✅（168 passed：端到端冒烟主熔断切备 + 冷却恢复切回；保护清单三态熔断手写 Redis Lua） |
| 2 | React 管理台 + ECharts 仪表盘 + Playground | pending | dogfood 通过 |
| 3 | Locust 压测：200 并发流式 5min | pending | 错误率 <1%，记录 P95 |
| 4 | teach 检查点：重试、熔断、负载均衡、CI/部署 | pending | 讲解通过 |
| 5 | 📌 简历上墙点 2（压测数字入 R 部分） | pending | 简历完整版 |

## 关键决策记录

| 日期 | 决策 | 依据/ADR |
|------|------|---------|
| 2026-09-10 | 采用 spec-kit 流程管理 feature（001-openai-compat-gateway = W1） | PROJECT_CONTEXT 任务 0 |
| 2026-09-10 | constitution 以 PROJECT_CONTEXT 第 2/8 节为源填充 | 治理原则需可执行 |
| 2026-09-11 | /clarify 答案：鉴权 = .env 固定 Key+Bearer（Q1:B）；流式 usage = 无条件注入 include_usage、无 usage 返 0、估算 W3（Q2:A）；models 来源 = DB+种子（Q3:B）；建表 = 全量 7 表（Q4:A）；验收 SDK = JS/TS（Q5:B） | Asize 亲自回答 |
| 2026-09-11 | Provider 接口定稿候选 B：模板方法基类（research.md D1） | design-an-interface 两版对比，W1 任务 2 要求 |
| 2026-09-11 | W1 零新增依赖：渠道密钥走 env，`api_key_encrypted` 存占位，加密方案 W4 走 ADR（research.md D2） | 规避停机点 3 触发，最小改动 |
| 2026-09-12 | Asize 确认 plan/tasks，停机点 2 解除；从 T001 开始 Phase 1。构建工具定 uv（本机 0.12.7，uv.lock 入库） | 任务 0 决策 |
| 2026-09-12 | Phase 2 决策三则：① 种子因 DDL 无唯一约束不用 ON CONFLICT，改"查重→插入/同步"实现幂等（规避改 DDL 停机点）；② pytest 增 `pythonpath=["."]` 以便 tests/ 导入 app.*；③ 鉴权不强制 `sk-` 前缀（conftest 的 key 无前缀），"前缀格式"分支 = Authorization scheme 校验 | 执行期工程决策 |
| 2026-09-12 | Phase 3 前置（Asize 确认）：.env 真实 DeepSeek key 用 `BASE_URL`+`API_KEY` 键名（非项目约定 `DEEPSEEK_API_KEY`）；采用"让配置适配现有键名"——改 config.py Settings 读这两个环境变量（或别名），不动 .env。key 值永不打印/入 git | 本会话交接决策，新窗口照做 |
| 2026-09-12 | Phase 3 前置完成：config.py 用 `AliasChoices("DEEPSEEK_*", "*")` 双键名兼容；env_file 从相对 `.env` 改为指向项目根的绝对路径（修本地 backend/ 目录运行读不到 .env 的问题）。已从 backend/ 与根目录两处复验 base_url/api_key 均读到真实值（只验非空不打印 key） | 执行结果，T010 可开始 |
| 2026-09-12 | Phase 3 真机诊断出真实渠道为 SenseAudio 开放平台（.env BASE_URL=api.senseaudio.cn，非官方 DeepSeek）。Asize 决策：① base_url 代码层自动规整补 `/v1`（官方与中转均兼容，换官方 key 不再改 .env）；② 种子模型更新为平台实际可用 ID（deepseek-chat→`deepseek-v4-flash-0731`，deepseek-reasoner→`senseaudio-s2`），渠道 base_url 同步 | Provider `_normalize_base_url()` + seed.py 更新，CP3.1 据此通过 |
| 2026-09-12 | Phase 4 执行期设计：SSE 解析器定稿为「同步状态机 SSELineParser（feed/finish/parse_full，事件级结算）+ sse_events() 异步适配」双形态——同步核心保单测直白，增量 feed 供 httpx aiter_lines 流式消费；坏 JSON 事件跳过（容错不透传，防 SDK 侧崩）、EOF 无 [DONE] 自然收敛（上游中断截断）；转发错误映射复用 T011 `_map_upstream_error` | forward.py 单一实现，T015 单测全部走同步核心 |
| 2026-09-12 | Phase 5 执行期设计：usage 提取沿用 SSE 解析器双形态定式——同步核心 `UsageTracker`（feed 原样透传 + 提取 usage；finish() 在上游未返 usage 时合成 usage=0 兜底 chunk 事件，位次=流尾最后一个产出，gateway 的 [DONE] 前）+ `with_usage()` 异步适配包 `sse_events`；provider.chat_stream 换用 `with_usage(sse_events(...))`。合成事件采样第一条 chunk 的 id/created/model，choices=[]（OpenAI usage chunk 语义） | SC-002 形式保证（调用方末尾总拿到 usage 对象）；透传语义不破坏 US2（坏 JSON 仍跳过、[DONE] 仍收敛） |
| 2026-09-12 | Phase 6 执行期设计：T022 DB 来源取「离线桩 AsyncSession」（monkeypatch gateway 模块，假会话模拟 DB 层 enabled 过滤，TestClient 走真实 app + lifespan）而非真实测试库——与现有单测 mock 外部依赖风格一致（T014 MockTransport），最简且离线可复跑、零 Docker 依赖；T021 created/owned_by 表无列 → 协议壳层静态默认值（created=0 / owned_by="onehub"，对齐测试断言） | 新窗口决策（交接决策点），US4 验收语义「未启用不出现」在单测由桩模拟、真机验收兜底 |
| 2026-09-12 | Phase 7 执行期设计（T023，路线 B 变体）：一键环境选「compose service 级 command shell 串」而非 Dockerfile ENTRYPOINT 脚本——新发现：原镜像无 alembic/seed.py（build context=backend/ 够不到根 scripts/），故先扩 context 到仓库根 + 新建 .dockerignore（挡 .env 真实 key 进构建上下文，密钥红线）+ 镜像实际 COPY alembic.ini/alembic/scripts/seed.py 自包含；command = `alembic upgrade head && PYTHONPATH=/app python scripts/seed.py && exec uvicorn`。seed.py 零改动（容器内 project root=/app，其 parents[1]+/backend 定位失效但 sys.path 插入不存在路径无害，PYTHONPATH=/app 接管 import app）；uvicorn 用绝对路径 .venv/bin 直调避免 uv run 潜在联网 sync | 空库 down -v 一键重建（Running upgrade + seed）与重复 up 幂等（零重建零重复种子）双验证通过；无新脚本文件、业务代码零改动，外科手术式 |
| 2026-09-12 | Phase 8 执行期设计（T025）：验收脚本用 `scripts/acceptance/`（package.json + verify.mjs，openai ^7.15.0），**独立 `GATEWAY_BASE_URL` 变量**——根 .env 的 `BASE_URL` 是渠道上游键名（Phase 3 决策），直接读会污染 SDK 目标地址（首轮全 404 实测根因）；只从 .env 提取 `GATEWAY_API_KEY`。模型 ID 用种子实际 ID（quickstart 断言 4 的 deepseek-chat 是旧 ID，不照抄）。收尾 `client.close()`+`process.exitCode` 而非 `process.exit()`（Windows/libuv 未关句柄断言崩溃）。SC-004 定义一个关键语义：未知 model = HTTP 404 + `type=invalid_request_error` + `code=model_not_found`（OpenAI 官方语义，type 不是 model_not_found） | 验收脚本实测 5/5 exit 0 + SC-004 三分支 PASS（httpx 防 GBK） |
| 2026-09-12 | 当前状态进 W2 | W1 收尾三份提交落地（408b786/24a18f4/13b4ed7），遗留 1 关闭 | — |
| 2026-09-12 | W2 依赖决策经 ADR-0001（docs/adr/0001-w2-jwt-redis-deps.md）Asize 批准：引入 pyjwt（管理面 JWT）+ redis-py（令牌桶 Lua/幂等键载体）；JWT/Redis 客户端非保护清单项目，算法本体（Lua 令牌桶）仍手写；任务 1 范围定 min：auth + keys CRUD | 停机点 3 解除；uv add redis pyjwt 落 pyproject + uv.lock + 容器镜像自动携带 |
| 2026-09-13 | W2 任务 3 执行期设计（dev-tdd）：限流落 `app/services/rate_limit.py`——TokenBucket 封装 Redis EVAL（KEYS=rate:{key_id}:{dim}，ARGV=capacity/rate/s/now/requested，HSET t+ts+EXPIRE 3600，返回 {1,0} 放行 / {0,wait} 拒绝，wait=ceil((requested-t)/rate) 兜底 1s）；`_refill` 纯函数（min(capacity, t+max(0,now-ts)*rate)）供单测公式对拍。并发槽不引入 asyncio.Semaphore 的 try-acquire 限制（无非阻塞原语），改自维护计数 `_in_flight`——单线程协作调度下检查→自增无 await 间隙，等价 Semaphore 语义。端点接入：`RateLimitError` 扩展 `retry_after` → `_openai_error_handler` 输出 `Retry-After` 头；流式槽位在 `_stream_events` finally 释放（断连不泄漏）；main lifespan 注入 Redis 单例（decode_responses=True，aclose 配对） | 单测依赖注入用 FastAPI `dependency_overrides`（monkeypatch 对路由注册期捕获的依赖引用无效，500 实测根因）；event_loop fixture 被 pytest-asyncio 0.26 弃用 → 删除后偶发 "no current event loop" 消失 |
| 2026-09-13 | **W2 任务 3 完成**：提交链 08a58fb（fix test event_loop）→ bf644ff（feat rate-limit）→ b655dcb（test rate-limit），79 passed（58 → 79）；真 Redis 冒烟 5 项 IO 全过（满桶放行/Retry-After ceil/补 token 对拍/4 桶 28 并发恰 20 放行零超发/双维度合并短路） | 下一步任务 4：幂等键（Idempotency-Key，Redis 存储手写，保护清单） |
| 2026-09-13 | **W2 任务 4 执行期设计（dev-tdd）**：幂等落 `app/services/idempotency.py`——双键设计 `idem:{key_id}:{ik}`（响应缓存，EX=24h 幂等窗口）+ `:claim`（在途占位，EX=30s 兜底崩溃残留）；手写 `IDEMPOTENCY_LUA`（GET 缓存 → GET 占位 → SET 占位 return {status,value}，EVAL 内 read-modify-write 原子防并发双转发）；三态 `cached 回放 / claimed 轮询 / acquired 得主`，CLAIMED 在 `_wait_result` 内消化（得主写缓存→回放 / 占位消失→接管转发 / 超时 5s→409）；端点接入在限流后、转发前，仅非流式生效（流式 SSE 缓存=重放整段事件流成本高收益低，忽略该头）；失败路径（上游抛错 + 槽满 429）一律 `cancel` 释放占位否则后来者永久 409；不校验同 key 不同 body（Stripe 同款假设，信任调用方唯一 key 唯一请求） | 提交链 46105bc(test RED)→3bcbf2e(feat GREEN)→951459e(fix test)；92 passed（79→92）；真 Redis 冒烟 IO 全过（缓存回放/10 并发恰 1 得主/撤销可重试/Lua 语义对拍） |
| 2026-09-13 | **W2 任务 6 产出**：security-best-practices 审查（docs/security_review_w2.md）——FastAPI 安全规范主动审计，未发现 Critical/High 可利用漏洞；2 项 Medium（/docs 公开暴露、Redis 无认证暴露）+ 6 项 Low/观察（secret_key 默认值守卫、body 无大小上限、无安全响应头、PBKDF2 100k 迭代、JWT TTL 12h、LoginRequest 无下限）；保护清单核查四组件均无注入面。简历初版（docs/resume_draft.md）：W1–W2 成果四块（协议兼容链路 / SK-Key 鉴权 / Redis 限流 / 幂等键）+ 量化证据表，全部数字有命令级证据 | W2 阶段 1-4/6 达成交付；teach 材料已产出待集中自验；下一步进 W3 计费引擎（任务 1 models 定价 + 余额预检） |
| 2026-09-14 | **W3 任务 1 执行期设计（dev-tdd，Asize 跳过澄清按推荐默认）**：402 走新增 `InsufficientBalanceError`（继承 OpenAIError，status=402 / type=insufficient_quota / code=insufficient_balance，OpenAI 生态支付语义）；余额预检不做超额扣减只做**成本估算**——`estimate_cost(input_price×in_tokens + output_price×out_tokens)`，`balance < 预估成本` 才 402；Redis 缓存余额 `balance:{tenant_id}`（TTL 短缓存），未命中查库回填，命中不查库（满足"预检不查库"验收）；models 定价 CRUD——models 全局表无 tenant，经 require_admin JWT 即管理身份；POST 查重式幂等（表无唯一约束）→409；DELETE 硬删（usage_records.model 为 VARCHAR 非 FK，删安全）；Numeric-Decimal → float 以 JSON 序列化；P1（超额拦截/计费回执）本轮跳过并入收尾前统一加固 | 提交链 feat(test)+docs；111 passed（92→111）；排障：admin_models 首次全量报 Decimal 序列化异常——测试请求体传 Decimal 对象、httpx 序列化 JSON 失败（JSON 本无 Decimal 语义），改传 float 由 Pydantic 落 Decimal；下一步任务 2 用量事件 → Redis Stream → Celery 异步落库 |
| 2026-09-14 | **W3 任务 2 执行期设计（dev-tdd，Asize 已批准 ADR-0002）**：用量事件链路落 `app/services/usage_events.py`（Producer XADD + Consumer 幂等落库）+ `app/workers/billing.py`（Celery app + `read_and_process` 消费——ADR 方案 A 经停机点 3 批准引入 `celery[redis]`）；幂等锚点 = `usage_records.request_id` unique（重发/消费重放零重复行）；网关非流式/流式两路径在转发生效后 XADD（缓存回放/失败不产生事件）；worker 入 compose（复用 api 镜像，depends_on pg+redis）。冒烟暴露并修复两个真实生产缺陷：① Redis XADD 拒绝 None 字段 → `UsageProducer.emit` 落流前过滤可空可选字段（channel_id/latency_ms）；② worker 用 `decode_responses=True` 读出全字段为 str → `UsageConsumer.consume` 用 `_as_int` 收敛 int/None 再落库 | 提交链 test(usage_events)+feat(usage_events,workers,compose)+docs；116 passed（111→116）；真 Redis+PG 冒烟：XADD 11 事件（10 唯一 +1 重复）→ read_and_process 消费落库 10 行、范围行=去重行=10（零重复，链路通畅） |
| 2026-09-14 | **W3 任务 3 执行期设计（dev-tdd，对账驱动）**：乐观锁扣减落 `app/services/billing.py`——`charge_balance`（先读最新 version → `UPDATE ... WHERE tenant_id=? AND version=?` 单行事务级互斥 CAS，rowcount=0 说明并发已提交 → 重读重试；无余额行/重试耗尽抛 `ChargeConflictError` 交 worker 回滚重试，不静默丢钱）+ `compute_cost` 倍率计价（实际 token×单价，与预检 estimate_cost 上限估算区分）+ `UsageConsumer.consume` 在幂等落库后逐事件扣减租户余额并失效 Redis 缓存 + worker 传 redis 连接。**对账暴露真实缺陷**：重试上限 5 在 50 路并发下不收敛（同波并发者每波仅 1 人读到未被消费的 version，第 k 个需约 k 次重试）→ 上限提至 100（覆盖 50 并发 + 余量，冲突瞬态重读即收敛） | 提交链 test(charge) RED→feat(charge) GREEN→fix(charge) 上限→test(reconcile)；126 passed（116→126）；对账脚本 reconcile_charge.py 四段 ALL PASS（幂等零重复/余额精确一致/幂等重放零新增/50 路并发收敛） |
| 2026-09-14 | **W4 任务 1 执行期设计（dev-tdd）**：智能路由拆三层单测覆盖——① `circuit_breaker.py`（保护清单，手写 Redis Lua 三态：`HASH breaker:{cid}` 存 state/failure_count/opened_at，EVAL 内查询+迁移原子；CLOSED 计数放行 / OPEN 冷却期内拒绝、过冷却转 HALF_OPEN 放行探测 / HALF_OPEN 放行探测后据成败切回/重开）+ ② `router.py`（加权轮询 WeightedRobin 游标取模 + 指数退避含抖动纯函数，非保护清单但手写保简历叙事）+ ③ `smart_router.py`（编排：熔断过滤→加权挑选→失败退避重试→成功记好，MAX_TOTAL_ATTEMPTS=3）。网关接入：候选=同 model_name 多行 enabled 挂到的 channel（权重/可用性取自 `channels.status==healthy`），有候选走智能路由、无候选（单渠道旧语义/测试桩 channel_id=None）回退 app.state 单例保 126 基线与既有测试零改动；provider 按渠道惰性构造缓存（`app.state.channel_providers`，lifespan 关闭统一 aclose）。种子加备份渠道 `deepseek-backup`（weight=5 占位 key）+ 同名模型变体。**验收证据**：端到端冒烟（真实 SmartRouter + FakeBreaker，stub 上游）主渠道熔断 OPEN → 自动切备份、冷却恢复 → 切回主渠道；全部候选不可用 503 | 提交链 test(circuit_breaker)+test(router)→feat(circuit_breaker)+feat(router)→feat(smart_router)+test(smart_router)→feat(gateway+main+seed)+test(gateway_router 含冒烟)；168 passed（126→168）；下一步任务 2 React 管理台 |

## 遇到的错误

| 错误 | 尝试次数 | 解决方案 |
|------|---------|---------|
| seed ON CONFLICT 报 `no unique or exclusion constraint matching`（channels.name 无唯一约束） | 2 | 改查重后再插入；已有行同步关键字段，事务内完成（不改 DDL，规避停机点 3） |
| alembic/TestClient 读取 UTF-8 中文 ini 报 GBK 解码错 | 2 | alembic.ini 注释改英文（configparser 用 locale 编码读） |
| pytest 找不到 `app` 模块 | 2 | pyproject `[tool.pytest.ini_options] pythonpath=["."]` |
| TestClient 触发 500 时直接抛异常 | 2 | `TestClient(..., raise_server_exceptions=False)` 让 ServerErrorMiddleware 转交统一出口 |
| 本机 uvicorn 启动后 model 校验请求全部 500 | 2 | 根因：.env 的 DATABASE_URL 用容器主机名 `pg`，本机进程解析失败（`socket.gaierror`）。本机运行用环境变量覆盖 `DATABASE_URL=postgresql+asyncpg://onehub:onehub@localhost:5432/onehub`（.env 供 compose 内部使用，不一致属设计选择而非 bug） |
| 真转发返回 502/404（message: upstream server error） | 3 | 根因：真实渠道为 SenseAudio 中转，只认 `/v1` 前缀路径且模型 ID 非 deepseek-chat。解决：`_normalize_base_url()` 自动补 `/v1` + 种子/DB 模型 ID 更新，CP3.1 后 200 |
| 幂等单测 3 用例 FAIL（`result[0]` 期望 `'claim'` 实得 `'claimed'`） | 2 | Lua 真实返回语义为 `'claimed'`（占位已存在），测试首写 `'claim'` 与实现不一致——对齐测试预期而非改实现（fix 提交）；连带 FakeRedis 补占位键写入语义、ApiKey fixture 补显式 `id=1` |
| W3 任务 2 冒烟：XADD 报 `Invalid input of type: 'NoneType'` | 1 | Redis XADD 拒绝 None 字段值，而网关流式路径 `channel_id` 可为 None → producer.emit 落流前过滤可空可选字段（channel_id/latency_ms） |
| W3 任务 2 冒烟：落库报 asyncpg DataError `'str' object cannot be interpreted as an integer` | 1 | worker 用 `decode_responses=True` 读 Stream，字段值全为 str（'10'/'1'）→ 消费端 `UsageConsumer._as_int` 落库前收敛 int/None |
| W3 任务 2 冒烟：`session.scalar` 同步调用未 await（coroutine never awaited） | 1 | 冒烟脚本查库改用 `await session.scalar(...)`（async_session 的 scalar 是协程） |
| W3 任务 3 对账 ③：50 路并发 charge_balance 抛 `ChargeConflictError after 5 retries` | 2 | 根因：重试上限 5 不足——同波并发者每波仅 1 人读到未被消费的 version（thundering herd），第 k 个并发者需约 k 次重试；上限提至 100（覆盖 50 并发 + 余量），重跑 ALL PASS |
