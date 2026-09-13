# OneHub 安全审查报告（W2 · 任务 6）

> 审查方法：OWASP / FastAPI Security 规范（`security-best-practices` skill 的
> `python-fastapi-web-server-security.md`，2026-01-27 版）主动审计模式。
> 审查范围：W1–W2 全部后端代码（app 工厂、鉴权、管理面、网关面、限流、幂等、
> 错误出口、部署配置、依赖锁）。
> 结论先行：**未发现 Critical/High 级可利用漏洞**；发现 2 项 Medium（生产暴露面）与
> 若干 Low/观察项。保护清单项目（令牌桶/幂等/SSE/usage）经核查无注入面，
> 无需按"换库简化"处理。

## 1. 审计范围与证据

| 文件 | 角色 |
|------|------|
| `backend/app/main.py` | 应用工厂、lifespan、docs 暴露 |
| `backend/app/core/security.py` | Bearer 提取、SK-Key 表鉴权、密码哈希、JWT |
| `backend/app/core/config.py` | 配置（secret_key 等） |
| `backend/app/core/errors.py` | 统一错误出口（堆栈泄露面） |
| `backend/app/api/admin/{auth,keys}.py` | 管理面注册/登录/Key CRUD |
| `backend/app/api/v1/gateway.py` | 网关面 /v1 端点 |
| `backend/app/services/{rate_limit,idempotency,keys}.py` | Redis Lua 限流/幂等、Key 哈希 |
| `backend/app/schemas/admin.py` + `chat.py` | 输入契约 |
| `backend/Dockerfile` + `docker-compose.yml` | 部署配置 |
| `backend/uv.lock` | 依赖锁定版本 |

## 2. 通过项（符合规范，逐条取证）

| 规则 | 结论 | 证据 |
|------|------|------|
| FASTAPI-DEPLOY-001 生产禁 --reload | ✅ | Dockerfile CMD 与 compose command 均无 `--reload`（无 watchfiles） |
| FASTAPI-DEPLOY-002 生产禁 debug | ✅ | `FastAPI(...)` 无 `debug=True`；`_unhandled_error_handler` 500 兜底返回通用 `"internal server error"`（errors.py:140-145），零堆栈/内部细节泄露 |
| FASTAPI-AUTH-001 鉴权统一走依赖 | ✅ | 网关面 `/v1/models`、`/v1/chat/completions` 均挂 `require_gateway_api_key`；管理面 keys 全挂 `require_admin`；无"忘了挂"路径 |
| FASTAPI-AUTH-002 禁 URL 传令牌 | ✅ | 全部 `Authorization: Bearer` 头；无 query token |
| FASTAPI-AUTH-003 密码强哈希 | ✅(观察) | PBKDF2-HMAC-SHA256 + 随机盐(secrets.token_hex) + constant-time 比对（hmac.compare_digest）；响应零 password_hash。观察：迭代 100k < OWASP 建议 600k，尊重"零新依赖"约束暂不换 Argon2（见发现 7） |
| FASTAPI-AUTH-004 JWT 严格校验 | ✅(观察) | `decode(..., algorithms=["HS256"])` 显式白名单（防 alg=none/混淆）；payload 仅 sub/tenant_id/iat/exp 无机密；exp 由 pyjwt 默认验证。观察：无 iss/aud（单服务签发边界，可接受）；TTL 12h（见发现 8） |
| FASTAPI-AUTHZ-001 对象级/属性级授权 | ✅ | keys list/revoke 均 `where(tenant_id == claims["tenant_id"])`（keys.py:76-98）跨租户不可见/不可删；`_public_fields` 脱敏不含 key_hash/明文；register/login 响应显式字段无 password_hash |
| FASTAPI-CSRF-001 | ✅ 不适用 | 纯 Bearer header 认证，无 cookie 会话 → CSRF 不适用 |
| FASTAPI-VALID-001 输入 schema 化 | ✅(观察) | 全部 body 为 Pydantic 模型；`ChatCompletionRequest` 的 `extra="allow"` 是协议透传的 W1 既定设计，该字段仅转发上游不落库（mass assignment 无落点） |
| FASTAPI-RESP-001 防过度暴露 | ✅ | 管理面用 `_public_fields`/显式子段白名单输出 |
| FASTAPI-INJECT-001 SQL 注入 | ✅ | 全 SQLAlchemy ORM 参数化查询（select/where 表达式），零字符串拼接 |
| FASTAPI-INJECT-002 命令注入 | ✅ | 业务代码无 subprocess/os.system/shell |
| FASTAPI-SSRF-001 | ✅ | 唯一出站请求是 provider.chat → base_url 来自 env 可信配置；无用户可控 URL 抓取功能 |
| FASTAPI-CORS-001 | ✅ | 无 CORSMiddleware（纯 API 默认全禁，未放通任意域） |
| FASTAPI-SUPPLY-001 依赖补丁 | ✅ | uv.lock：starlette **1.6.0**（历史 CVE 修复线 0.27/0.40/0.49.1 均为最新上游修复版本）；fastapi 0.141.1 / uvicorn 0.52.4 / httpx 0.28.1 均为新版本 |
| 密钥红线 | ✅ | `.env` 不入 git（.gitignore/.dockerignore 双重）；key 值绝不打印/入文档；Key 明文仅创建时一次返回 |

## 3. 发现项（按严重度降序）

### 发现 1 — FASTAPI-OPENAPI-001：管理面 /docs 公开暴露
- **严重度**：Medium（生产为 High，当前仅本地/容器开发环境）
- **位置**：[main.py](file:///e:/ASUS/桌面/Resum/003-Project/OneHub/backend/app/main.py#L57-L63) `FastAPI(title=..., docs_url="/docs")`
- **证据**：`docs_url="/docs"` 显式开启，且与网关面、管理面（/api/auth、/api/keys）同 app——`/docs` 可完整看到管理面 API 结构（含登录取证、Key CRUD 语义）。
- **影响**：信息泄露放大器——攻击者拿到管理面端点结构与字段约束，降低后续探测成本。
- **修复**（生产）：`docs_url=None, redoc_url=None, openapi_url=None`（或按 env 开关），由 `.env` 生产模式置关。
- **缓解**：edge 反向代理对 `/docs*` 仅允许内网；当前本地开发不需要。
- **误报说明**：W1/T002 验收标准就是"`/docs` 可访问"（US 验收），本地保留是特性不是缺陷；纳入即可，不需现在改码。

### 发现 2 — Redis 暴露 + 无认证（开发编排面）
- **严重度**：Low（本机开发）；生产 High 面
- **位置**：[docker-compose.yml](file:///e:/ASUS/桌面/Resum/003-Project/OneHub/docker-compose.yml#L37-L48) `redis` 端口 `6379:6379` 全主机发布，`redis:7-alpine` 未设 `requirepass`
- **证据**：`ports: - "6379:6379"` 绑定 0.0.0.0；容器命令无密码。
- **影响**：宿主机上任何进程/同网段可达者可直接读写网关 Redis——污染幂等缓存（回放伪造响应：idempotency.py 缓存值 `json.loads` 后直接作为 API 响应）、清空/改令牌桶状态。防御面当前依赖"开发机可信"。
- **修复**（生产）：Redis 不发布到主机或绑 localhost；容器启用 `requirepass` + 网关客户端带密码。pg 同理（onehub/onehub 默认凭据 + 5432 发布）。
- **缓解**：本地可信环境可接受；若投递/演示需公网可达，必须先做上述修复。

### 发现 3 — config 默认 `secret_key="change-me"`
- **严重度**：Low（现环境已由 .env 覆盖）；若生产漏配则 High
- **位置**：[config.py](file:///e:/ASUS/桌面/Resum/003-Project/OneHub/backend/app/core/config.py#L34) `secret_key: str = "change-me"`
- **证据**：JWT 签发/校验全部使用 `get_settings().secret_key`（security.py:70,77）。默认值可被任意人猜到 → 可伪造管理面 JWT（create key / 绕过管理面）。
- **影响**：仅在 .env 未注入 SECRET_KEY 且服务对外可达时成立。
- **修复**：生产启动前断言 `secret_key != "change-me"`（lifespan 内检查一次即弃），或 .env.example 强调必填。
- **误报说明**：conftest 注入的 32+ 字节 SECRET_KEY 与 .env 已有值当前都正常；本项是"默认值守卫"缺失。

### 发现 4 — FASTAPI-LIMITS-001：无显式请求体大小上限
- **严重度**：Low–Medium
- **位置**：[gateway.py](file:///e:/ASUS/桌面/Resum/003-Project/OneHub/backend/app/api/v1/gateway.py#L87-L94) `chat_completions` 接受任意大小 JSON body
- **证据**：无 body 尺寸限制；chat 的 `messages` 由调用方任意填充。
- **影响**：内存/CPU DoS——超大 body 拖垮 JSON 解析与后续逐条处理。真实场景：LLM 网关面向公网。
- **修复**：edge（nginx `client_max_body_size`）或应用层对 `Content-Length`/流式 body 限长。
- **缓解**：限流（RPM/TPM）已挡"频率型"滥用，未挡"单次巨型"；本地开发可接受，生产必做。

### 发现 5 — FASTAPI-HEADERS-001：无安全响应头
- **严重度**：Low
- **位置**：[main.py](file:///e:/ASUS/桌面/Resum/003-Project/OneHub/backend/app/main.py) 无中间件设置响应头
- **证据**：全 app 无 `X-Content-Type-Options` 等安全头。
- **影响**：纯 JSON API（无 HTML 渲染/无 iframe 场景）风险低；`X-Content-Type-Options: nosniff` 属于廉价纵深防御。
- **修复**：Starlette middleware 统一加 `X-Content-Type-Options: nosniff`（1 行）；其余头（CSP/Frame-Options）纯 JSON API 无必要。

### 发现 6 — （观察）LoginRequest 未设长度下限
- **严重度**：Low（观察）
- **位置**：[schemas/admin.py](file:///e:/ASUS/桌面/Resum/003-Project/OneHub/backend/app/schemas/admin.py#L17-L21) `email: str` / `password: str` 无约束
- **影响**：无实际攻击面（登录只做比对，不落库不记录）；仅与 register 的 min_length 不一致，属一致性打磨项。

### 发现 7 — （观察）PBKDF2 迭代 100k 低于 OWASP 建议
- **严重度**：Low（观察）
- **位置**：[security.py](file:///e:/ASUS/桌面/Resum/003-Project/OneHub/backend/app/core/security.py#L32) `_PBKDF2_ITERATIONS = 100_000`
- **影响**：暴力破解成本低于 Argon2id/600k 迭代；对简历叙事（"密码哈希"卖点）可写实为"PBKDF2 + 随机盐 100k 迭代"，如需更强再提迭代或引 argon2-cffi。
- **修复**（可选）：迭代提到 600k（登录/注册延迟 ≈ 6×，本地与登录流频率低可接受）；`verify_password` 已支持存储串内迭代数，存量哈希停机升档。

### 发现 8 — （观察）JWT TTL 12h / 无角色细分
- **严重度**：Low（观察）
- **位置**：[security.py](file:///e:/ASUS/桌面/Resum/003-Project/OneHub/backend/app/core/security.py#L33) `_ACCESS_TOKEN_TTL = timedelta(hours=12)`
- **影响**：12h 管理面会话偏长；当前仅 admin 单角色，`claims["role"]` 未参与授权分支。本阶段租户即隔离边界，够用；后续管理台多角色时再改小 TTL + 角色断言。

## 4. 保护清单核查（resume 卖点逐一手写实现的健壮性）

- **令牌桶（rate_limit.py）**：Lua 脚本 KEYS/ARGV 全参数化，无注入面；EVAL 原子性经 28 并发冒烟实证。✅
- **幂等键（idempotency.py）**：Lua 三态原语同参数化；缓存回放的 `json.loads` 输入 = 网关自身写入（Redis 可信前提下安全，见发现 2）。✅
- **SSE 透传 / usage 解析（forward.py）**：纯数据面，无 shell/文件/模板；坏 JSON 跳过防 SDK 崩。✅
- 结论：**无"换库简化"类建议可驳回项以外的问题**；上述 8 项发现均不触碰保护清单实现本体。

## 5. 建议修复优先级（P0 紧急 / P1 短期 / P2 留档）

| 优先级 | 项 | 改动量 |
|--------|-----|--------|
| P1 | 发现 3：lifespan 断言 secret_key 非默认 + Discover-4 body 限长 | ≤5 行 |
| P1 | 发现 1：`docs_url` 按 env 生产关（本地保留） | ≤3 行 |
| P2 | 发现 2：演示/生产前 Redis+pg 加认证、不放外网 | 部署配置 |
| P2 | 发现 5：中间件加 nosniff | 1 行 |
| P2 | 发现 7/8：迭代升档、TTL 收紧 | 常量改动 |

> 本报告为快照审查（W2 末）。W3/W4 引入计费引擎与 React 管理台后需再跑一轮
> （新增面：余额扣减并发、Redis Stream、前端 XSS/CSRF、管理台 CORS）。