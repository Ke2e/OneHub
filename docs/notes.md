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

## 2026-09-13 周 · W2：多租户鉴权 + 限流 + 幂等五讲

> W2 全部实现（任务 1-4）已在 progress.md 留证。按 AGENTS.md 停机点约定：
> teach 讲解统一延后到项目完成后集中自验，本材料照常产出，讲解时对照本文件。

### 1. API Key 体系：租户隔离 + 哈希存储（任务 1/2）

**三级模型**（tenant → user → api_key）：Key 挂在用户下，用户挂在租户下——表格天然带租户维度，任何查询先过滤 `tenant_id`，"租户隔离"不是加一段 if，而是数据模型自带的结构。

**SK- Key 生成与存储**（`app/services/keys.py`）：

- `generate_sk_key()` 调一次**只返回一次明文**（`sk-` 前缀标识 + 留存 `key_prefix` 供管理台展示/回忆）
- 入库只存 `SHA-256 hex`（`key_hash`，CHAR(64)）：数据库被拖库也拿不到可用 Key，这是"不存明文"的第一性原则
- 校验路径：请求带明文 → 网关 `hash_sk_key()` 算 SHA-256 → 查 `key_hash` 命中 → 再校验 `status`/`expires_at`（W2 任务 2 中间件）

**为什么哈希还不够——瀑布式的三层校验**（`require_gateway_api_key`）：

1. 明文不存在于任何查询结果，先哈希（SHA-256 无法逆推，即使拿到 DB 也没用）
2. `status != active` → 401（软删/吊销立即可用，不用真删行——保留审计痕迹）
3. `expires_at` 已过 → 401（Key 期限自动化失效）

**管理面密码/会话用另一套**：PBKDF2（加盐慢哈希，抗彩虹表）+ JWT HS256（无状态会话，`expires` 声明内置过期）。业务 Key 的介质是调用方持有、网关只验哈希；管理面身份是需要会话状态的交互流程——两套密码学手段服务于两种不同的信任模型。

**面试讲法**：Key 是"长期静态凭证"（哈希+吊销+过期），JWT 是"短暂动态会话"（签名+过期自动失效）；白名单是授权粒度（key 级模型权限），与鉴权（你是谁）解耦——鉴权放行后才能谈授权。

### 2. 限流四算法与取舍（任务 3）

网关面每 Key 双维度：**RPM**（请求数/分钟）+ **TPM**（token 数/分钟）。四算法逐个过：

| 算法 | 原理 | 缺点交给下一级解决 |
|------|------|------|
| 固定窗口 | 窗口内计数，满则拒 | 窗口边界"突刺"：边界两侧各满一窗 = 2 倍速率 |
| 滑动窗口 | 细粒度窗口加权 | 实现复杂，Redis 需多键 + 导数 |
| 漏桶 | 恒速出水，队列削峰 | 流量整形严格，突发能力受限 |
| **令牌桶** | 以 `rate` 补 token、以 `capacity` 存突发 | 应对"突发 + 平均"双约束的最佳平衡 |

**令牌桶数学**（与 Redis EVAL 同一公式，代码里有 `_refill` 纯函数对拍）：

```
t = min(capacity, t + (now - ts) * rate)   # 补 token，封顶
是否放行: t >= requested ? 扣减 : 拒绝
拒绝提示: wait = ceil((requested - t) / rate)   # Retry-After 秒
```

**为什么用 Redis 存状态**：多实例/多 worker 共享同一桶——桶状态若在进程内存，N 个实例就是 N 倍限额。Redis 是共享存储 + 原子脚本执行。

**面试讲法**：`capacity`（突发容量）与 `rate`（平均速率）是两个独立旋钮——`capacity=60, rate=60/60s` 意味着"突发 60 次请求，随后每秒恢复 1 次"，这正好是用户透传 SDK 的体验：能快速连续发，超了还要等。

### 3. Redis 原子性与 Lua：为什么 EVAL 是限流/幂等的地基（任务 3/4）

**竞态问题**：`GET` 桶状态 → Python 里算 → `SET` 写回，两步之间存在 await 间隙——并发请求会读到同一个旧值，双双放行（双超发）或双双拒绝（误伤）。

**Lua 脚本**（`TOKEN_BUCKET_LUA`）：`EVAL` 把 read-modify-write **打包进 Redis 单线程执行**，脚本执行期间无任何并发间隙——多请求共享同一 Redis，脚本天然串行。"原子性不是靠锁，而是靠执行模型"。

**手写 Lua 的三段式**（幂等脚本同构）：

1. `GET KEYS[主]`（查缓存/查桶）
2. 分支判定 + `SET KEYS[次, EX ttl]`（占位/扣减）
3. `return {status, value}`（结果回传，客户端解析）

**注意坑**：NSString 返回 `{1,0}`，Python 侧要 `bool()`/`int()` cast——redis-py 返回的是 list，逐项 cast 防类型抖动；TTL 用 `EXPIRE`/`SET ... EX` 显式设置，防键永久残留（每次写都续期，热键不消亡）。

**面试讲法**：凡"读-改-写"三步在共享存储上就要原子化；Redis 的原子单位是 Lua 脚本，而不是单条命令；EVAL 模式下脚本是唯一能安全完成"条件扣减/条件占位"的载体。

### 4. 幂等键：重复请求不重复转发（任务 4）

**问题**：调用方超时重试、SDK 自动重试、客户端连点——同一个"只应生效一次"的请求可能到达多次。若每次都转发上游，就是**双倍扣费 + 双倍用量**。

**方案**（`app/services/idempotency.py`，保护清单手写）：

- 调用方对关键请求带 `Idempotency-Key` 头（规范建议 UUID，一个 key 对应"一次业务意图"）
- 网关以 `(key_id, idempotency_key)` 为 Redis 键，缓存**完整非流式响应体**，TTL 窗口 24h
- 同 key 再次请求 → 命中缓存直接回放首次响应，**不转发上游**（不重复计费）

**并发同 key 防双转发（核心难点）**：两个并发请求同 key 同时到达，都 miss 缓存——若都转发就双计费。解法是 Redis Lua **原子占位**：

```lua
-- KEYS[1]=响应缓存  KEYS[2]=在途占位
cached = GET KEYS[1]; if cached then return {'cached', cached} end
claim = GET KEYS[2];  if claim then return {'claimed', claim} end
SET KEYS[2] <uuid> EX 30          -- 只有第一个请求能 SET 成功
return {'new', ''}
```

- 返回 `cached` → 回放
- 返回 `new` → 本轮**得主**，继续转发，成功后 `complete`（写缓存 + 删占位）
- 返回 `claimed` → 已在途，**短轮询**等得主结果：
  - 得主写完缓存 → 回放
  - 得主失败并删占位 → 接管转发（失败可重试）
  - 超时（5s）→ 409，调用方重试

**失败路径**：得主转发失败/被 429 挡回必须 `cancel` 释放占位——否则后来者永远等到 409，幂等语义退化成死锁。占位键 TTL 30s 兜底崩溃残留。

**两个边界决策**：① 只对**非流式**生效（SSE 缓存 = 重放整段事件流，成本高收益低，流式请求忽略该头）；② 不校验同 key 不同 body（信任调用方为唯一请求用唯一 key——Stripe 同款假设）。

**面试讲法**：幂等的本质是"缓存 + 并发协调"两台戏——缓存解决重复，Lua 占位解决并发；没有原子占位，缓存方案在并发下会退化成"双转发"。

### 5. 并发控制：从进程内计数到 W3 乐观锁

**网关的并发上限**（Semaphore 语义）：限流管"速率"，还有一路是"同时在途"上限。`asyncio.Semaphore` 没有非阻塞 try-acquire（`acquire()` 会挂起），线程池方案在事件循环里会阻塞调度——所以用**自维护计数** `_in_flight`：

```python
if self._in_flight >= max_concurrent: return False   # 占满 → 429
self._in_flight += 1                                  # 检查→自增之间无 await
```

单线程协作调度下，"检查 → 自增"两步之间没有 `await`，不会被其它协程插入——天然原子，等价 Semaphore 的 try-acquire 语义。槽位必须 `try/finally` 配对释放：**流式请求的槽位在生成器 finally 释放**（断连也不泄漏），非流式同步释放。

**为什么这个阶段不碰"跨实例锁"**：W2 的在途计数是进程内的（单实例语义）；跨实例的分布式锁、以及 W3 的**乐观锁扣减余额**（`UPDATE ... WHERE balance >= amount AND version = n`，靠受影响行数判断并发冲突）是不同层级的并发问题。演进顺序值得面试展开：进程内计数（简单、单机）→ 分布式锁（多机互斥）→ 乐观锁（无锁冲突重试，适合计费这种"冲突少、代价高"的场景）。

**面试讲法**：三个并发手段按"代价 vs 冲突频率"选型——计数最便宜但只限单机；锁最直接但会阻塞/踢皮球；乐观锁无阻塞但靠重试，是"写冲突少"类场景（计费）的最优解。
