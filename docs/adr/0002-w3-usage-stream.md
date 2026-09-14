# ADR-0002：W3 用量事件落库通道（Redis Stream + 消费队列）

- 日期：2026-09-14
- 状态：**已批准（方案 A：Celery）**——Asize 认可新增外部依赖 / 引入消息队列，停机点 3 解除
- 决策人：Asize

## 背景

W3 任务 2（`task_plan.md` W3 表第 2 行）：「用量事件 → Redis Stream → Celery 异步落库」。链路（`PROJECT_CONTEXT 6.3`）：

```
请求 → ... → 转发 + usage 统计 → ⑤XADD usage:{ts}（Redis Stream）
→ Celery 消费组读取 → 幂等(request_id 唯一索引) → 倍率计价
→ 乐观锁(version)更新 balances → 失效余额缓存
```

本 ADR 仅裁决**消息通道的选型与依赖**。计费幂等、乐观锁扣减仍属保护清单（手写），乐观锁落 balances 归 W3 任务 3，本任务只做前半段：**XADD 事件 → 消费 → 幂等落 `usage_records`**（验收 = 链路通畅）。

### 现状盘点

- `usage_records` 表已就位：`request_id` 全局唯一（幂等防重复落库，PDDL 9 节）——落库天然幂等，无需表变更。
- `redis`（redis-py 8.x，已在依赖）原生支持 `xadd/xreadgroup/xack/xgroup/xpending`（`redis/commands/core.py` 已核对）——**Stream 不新增依赖**。
- 网关尚无用量事件**发出端**：流式/非流式完成转发后，usage 结果在 `UsageTracker`/响应对象里，未 XADD。
- 无任何异步 worker 进程（compose 只有 api/pg/redis）。

## 待裁决点：消息消费模式

**方案 A（推荐）：Celery + Redis Stream 消费组**
- 新增依赖 `celery[redis]`，引入独立 worker 进程（compose 增 `worker` 服务）。
- Producer（网关）：转发完成后 `XADD usage:{ts}`（Redis Stream 原生存）。
- Consumer（worker）：Celery beat/task 调消费组 `XREADGROUP usage:group:consumer` → 幂等落库 → `XACK`。进程级隔离，重启/批处理/TTL 探测可复用同一 worker 骨架（PROJECT_CONTEXT 4 节 `app/workers/` 已预留）。
- `request_id` 唯一索引天然去重，重复消费零副作用；`XACK` 保证至少一次，配幂等转为精确一次。

**方案 B：纯 redis-py 自建消费者（不引 Celery）**
- 网关自行 `asyncio` 后台任务 `XREADGROUP` 消费同一 Stream。
- 零新增依赖，最轻量；但"异步任务"落在 FastAPI 进程内，与 W4 熔断探测、账单等需要独立 worker 的诉求不通用（PROJECT_CONTEXT 显式规划了 Celery worker 做账单/渠道健康探测）。

## 依据

- **PROJECT_CONTEXT 技术栈表**明确"异步任务：Celery + Redis（用量削峰落库、账单、渠道健康探测）"、目录结构含 `app/workers/`（Celery: billing.py / health.py）——Celery 是既定技术路线，本 ADR 是对既有规划的落地，非新增临时想法。
- **Redis Stream 是更优事缓冲**：与已有 `redis-py` 无缝、天然有序（时间序）、支持消费组多 consumer 水平扩展、`request_id` 幂等兜底防重复，是"削峰落库 + 断电可恢复"的标准组合（对比普通 list 队列：Stream 具备 ack/消费组语义）。
- **Celery 不碰保护清单**：计费幂等、乐观锁扣减仍手写（本任务未涉及乐观锁）；Celery 只是消息消费的执行框架（类比 W2 的 redis-py 只是 Lua 令牌桶的执行载体）。旁支 ponytail 审查豁免。
- **方案 A vs B**：都满足"链路通畅"验收；A 胜在契合 PROJECT_CONTEXT 既定架构（后续账单/健康探测复用 worker 骨架），B 只在"坚决零新增依赖"时优先。本任务存量幂等去重语义完整，A/B 均不会破坏保护清单卖点——选 A 以对齐全局架构。

## 影响

- `pyproject.toml` dependencies +`celery[redis]`（含 Kombu 等传递依赖，小组件）；`uv.lock` 更新入库；compose 增 `worker` 服务（镜像复用 api 的 Dockerfile，command 换 celery worker）。
- Stream key 命名 `usage:{ts}`（或 `usage:events`）；消费组名 `usage_group`；worker 复用 `DATABASE_URL`（独立 AsyncEngine 或复用模型 session）。
- 不改 DDL（`usage_records` 已含幂等唯一索引；本任务不新增列）。
- 保护清单组件维持手写，审查豁免不变。

## 待批准后执行顺序

1. `uv add "celery[redis]"`
2. RED 测试：事件载荷 schema → 消费去重（同 `request_id` 二插仅落一行）→ XACK 语义
3. GREEN：网关 XADD 发出端（非流式/流式两路径捕获 usage）→ worker 消费幂等落库 → compose 加 worker 服务
4. 对账冒烟：N 事件 → usage_records 行数 == N，零重复（本任务不含乐观锁，余额对账归任务 3）

## 决策权

请 Asize 批准：方案 A（Celery）或方案 B（纯 redis-py 自建消费者）。批准后即可 `uv add` 开工。