# ADR-0001：W2 引入 JWT 与 Redis 客户端依赖

- 日期：2026-09-12
- 状态：**提案（待 Asize 批准）**——触发停机点 3（新增外部依赖）
- 决策人：Asize

## 背景

W2（多租户鉴权 + 限流）两个任务需要之前未引入的运行时能力：

1. **管理端 JWT**（W2 任务 1）：`docs/PROJECT_CONTEXT.md` 6.4 定义管理面鉴权 = JWT（`POST /api/auth/login|register` 签发）。`config.py` 已预留 `secret_key` 字段，但 `pyproject.toml` 当前无任何 JWT 实现。
2. **Redis 客户端**（W2 任务 3）：`docs/PROJECT_CONTEXT.md` 6.1 令牌桶为「Redis + Lua」规格（`EVAL` 调用），必须经由 Redis 客户端执行。`docker-compose.yml` 的 redis 容器已就位，但 Python 侧无客户端依赖、`main.py` 也无连接。

## 决策

在 `backend/pyproject.toml` 的 `dependencies` 新增两个轻量依赖：

- **`pyjwt`**：RFC 7519 标准 JWT 实现，单文件、零传递依赖，用于管理面 JWT 签发 / 校验。
- **`redis`（redis-py）**：Redis 官方 Python 客户端（同步 + asyncio），用于令牌桶 Lua `EVAL`、幂等键（任务 4）。

## 依据（为何不手写）

- **JWT 不在保护清单内**：保护清单豁免范围 = 令牌桶 / 三态熔断器 / 计费幂等与乐观锁扣减 / SSE 透传 / usage 解析，全部为**协议或算法级手写卖点**。JWT 是标准安全协议（RS256/HS256、payload 结构、过期校验细节），手写属于「重造标准协议」，风险（alg confusion、签名校验语义）大于收益；pyjwt 成熟且轻量。
- **Redis 客户端同理**：令牌桶的**算法本身仍为手写 Lua**（规格 6.1 原样实现，EVAL 调用），简历卖点不受影响；redis-py 只是执行载体，不是「限流实现被换库」。
- **备选否掉**：`python-jose`（过重，加密算法超需）；手写 RESP 协议（重造标准库，违背 ponytail「用标准库」原则）；`redis-cell`（= 换掉手写令牌桶，直接违反保护清单，本 ADR 不涉及）。

## 影响

- `pyproject.toml` dependencies +2；`uv.lock` 更新入库。
- 容器镜像由 `uv sync --frozen` 自动带上新依赖，compose 无需改动。
- 保护清单五类组件维持手写，审查豁免不变。
- 管理面路由新增 `app/api/admin/`（目录结构 PROJECT_CONTEXT 4 节已规划），属既有规划落地，非改目录结构。

## 待批准后执行

1. `uv add redis pyjwt`（更新 pyproject + lock）
2. W2 任务 1：Tenant/User/ApiKey 三级模型消费 + `/api/auth/register|login`（JWT 签发）+ `/api/keys` CRUD（SK-Key 生成 `sk-` 前缀 + SHA-256 哈希存 `key_hash` + 模型白名单）
3. W2 任务 3：redis 客户端连接（app.state 单例）+ 6.1 Lua 令牌桶 + asyncio.Semaphore 并发限流