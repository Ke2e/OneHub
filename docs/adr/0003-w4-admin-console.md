# ADR-0003：W4 任务 2 管理台——引入前端栈 + 补齐后端管理接口

- 状态：**提案（待 Asize 批准）** —— 停机点 3
- 日期：2026-09-14
- 决策者：Asize
- 相关任务：W4 任务 2（React 管理台 + ECharts 仪表盘 + Playground，验收 dogfood 通过）

## 背景

W4 任务 1（智能路由 + 三态熔断）已完成（168 passed，已推送 origin/main）。W4 任务 2 需交付**管理台**：渠道/模型/Key 管理 + 熔断状态可视化 + ECharts 监控仪表盘 + Playground 对话试玩页。

前端技术栈由 PROJECT_CONTEXT §2 定死：**React 18 + TypeScript + Vite + ECharts**。项目迄今为纯后端（uv/pip 管理 Python 依赖），本次将首次引入前端 node 生态，属「新增外部依赖」，触发停机点 3，故先提案 ADR 待批准。

经 Asize 拍板的两项范围决策：
1. **全量一次交付**：后端补接口 + 前端全套（管理台 + 仪表盘 + Playground）
2. **Playground 走代理转发 + JWT**：新增管理面 play 端点，JWT 鉴权 → 复用网关智能路由转发到上游，前端 Playground 页直连该端点

## 现状盘点（已核实）

**管理面后端已有**：`auth`（register/login）、`keys`、`models` 三个路由，均挂 `require_admin`（管理面 JWT，claims=`{sub, tenant_id}`）。

**架构规划但尚未实现**（PROJECT_CONTEXT §4/§6.4）：
- `channels` CRUD（现有 `seed.py` 只写 deepseek-main / deepseek-backup 两条）
- `GET /api/dashboard/overview|channels`
- `GET /api/usage/logs`

**数据模型字段依据**（已核实 definitions）：
- `channels`：id/name/provider/base_url/api_key_encrypted/weight/status/failure_count/opened_at/created_at —— 熔断态 DB 冗余字段齐全
- `usage_records`：request_id(唯一)/api_key_id/channel_id/model/prompt_tokens/completion_tokens/latency_ms/status_code/cost/created_at + 复合索引 `(api_key_id, created_at)`
- `balances`：tenant_id(PK)/balance/version

## 架构级决策

### D1 前端技术栈与目录（PROJECT_CONTEXT 定死，落地细化）

- **运行时依赖**：`react@18`、`react-dom@18`、`echarts@5`、`react-router-dom@6`
- **构建/dev 依赖**：`typescript@5`、`vite@5`、`@vitejs/plugin-react`、`@types/react@18`、`@types/react-dom@18`
- 目录：新增仓库根 `frontend/`
- HTTP 用原生 `fetch`（不引 axios，控依赖面）
- 路由用 `HashRouter`（react-router-dom）：后端零配置、静态托管友好，适配 Vite dev 与 Docker/nginx 部署
- 建设顺序：`create-vite` 骨架（react-ts 模板）→ 裁剪 → 三页（管理台/仪表盘/Playground）
- **零后端运行时新增 Python 依赖**：所有新接口用现有 SQLAlchemy/FastAPI/aioredis 生态
- 保护清单四类核心算法（令牌桶/熔断/计费/SSE）不受影响，不换库

### D2 后端补齐接口（管理面，全挂 `require_admin`）

1. **channels CRUD**（`app/api/admin/channels.py`）
   - `GET /api/channels`：列表含熔断实时态（DB `status/weight/failure_count/opened_at` + 从 Redis `CircuitBreaker` 读三态 `state/opened_at/ripped_at`）
   - `POST /api/channels`：创建（name 查重→409；`api_key_encrypted` 存**占位**，密钥加密留待 W4 收尾前统一加固，与 W1 既有决策一致）
   - `PATCH /api/channels/{id}`：局部更新（weight/status 等）；`DELETE`：硬删（usage_records.channel_id 为可空外键，先校验无引用或置空）
2. **dashboard**（`app/api/admin/dashboard.py`）
   - `GET /api/dashboard/overview`：KPI —— 24h 请求量 / 今日 token 消耗 / 累计成本(=SUM(cost)) / 活跃渠道数(count status=healthy) / 租户余额列表
   - `GET /api/dashboard/channels`：各渠道维度聚合（请求数 / token / 平均 latency / 成本），供 ECharts（分流饼图 + 请求趋势线）
3. **usage logs**：`GET /api/usage/logs`（query：model/api_key_id/limit/offset，按 `created_at` 倒序分页，返回明细含 cost/tokens/latency），复用 `idx_usage_key_time`
4. **play 代理**（`app/api/admin/play.py`）：`POST /api/play/chat`
   - `require_admin` 鉴权 → 请求体 = OpenAI `ChatCompletionRequest`（stream 可选）→ 复用 `app.state.smart_router`（加权轮询 + 熔断过滤 + 重试）转发到上游
   - 流式响应以 `text/event-stream` 透传；非流式返回 JSON —— 与网关面同链路，只换鉴权边界
   - 前端 Playground 页即打该端点（管理台已登录拿 JWT）

### D3 接口无需改 DDL、无需迁移

全部新增端点只读/写现有 7 表字段，不动 `alembic/versions/`；`channels.api_key_encrypted` 沿用现有 Text 占位语义。**不触发禁改清单**。

## 备选考量（已在 Design 层面权衡，非本轮执行）

- 全栈引入 React vs 单文件 HTML+ECharts：PROJECT_CONTEXT 定死 React 栈，且承载简历「前端 + 联调」叙事，取定论
- play 代理走 JWT vs 前端直连网关 sk-key：Asize 拍板代理转发+JWT，避免前端持有/暴露 sk-key，鉴权语义统一管理台
- 每页表新增实时熔断态：由 `CircuitBreaker.get_state` 从 Redis 读（后端已有 `breaker:{cid}` HASH），前端只消费

## 影响

- 新增 `frontend/` 目录 + ~10 个 node 依赖（package.json + lock 入库）
- 新增 4 个后端 admin 路由模块（channels/dashboard/logs/play）+ 对应 schemas
- docker-compose 未来可加 nginx 托管前端静态产物（本 ADR 仅建骨架与服务端接口，nginx 容器化属 W4 任务 3/收尾范畴，不在本轮；Vite dev 供 dogfood 验证）
- 测试：后端新增接口沿用 `tests/_fake_db.py` FakeSession 桩 + `dependency_overrides` 离线单测；前端冒烟用 Vite dev + dogfood skill 验证交互

## 决策

请 Asize 批准 D1–D3，获批后：
1. 先做后端 4 个 admin 接口（dev-tdd，离线单测）
2. 再建 `frontend/` 骨架 + 三页
3. dogfood 验证 → 全量 pytest 回归 → 落三件套 → 提交追问是否 push