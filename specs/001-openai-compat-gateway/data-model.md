# Data Model: OpenAI 协议兼容网关（W1）

> Phase 1 输出。表结构以 PROJECT_CONTEXT 第 5 节 DDL 为唯一事实来源，此处标注 W1 使用范围与种子内容。迁移一次建齐 7 表（Q4: A 决议）。

## 实体关系

```text
tenants 1─n users            （W2 消费）
tenants 1─n api_keys          （W2 消费）
tenants 1─1 balances          （W3 消费）
tenants 1─n channels*         （隐含租户维度，W1 单租户语境）
channels 1─n models           （W1 读）
api_keys 1─n usage_records    （W3 消费）
channels 1─n usage_records    （W1 建表不写，W3 起写）
```

## 表清单（7 表全量建，W1 消费状态标注）

| 表 | W1 状态 | 说明 |
|---|---------|------|
| channels | **读**（seed 1 行） | 渠道定义：name/provider/base_url/api_key_encrypted(占位)/weight/status |
| models | **读写**（seed 2 行） | model_name/channel_id/input_price/output_price/enabled——/v1/models 数据源 + 请求 model 校验 |
| tenants | 建表不使用 | W2 多租户 |
| users | 建表不使用 | W2 管理端 JWT |
| api_keys | 建表不使用 | W2 SK- Key 体系 |
| usage_records | 建表不使用 | W3 计费落库（request_id 唯一索引届时做幂等） |
| balances | 建表不使用 | W3 乐观锁扣减 |

## W1 种子数据（scripts/seed.py，幂等 upsert）

- **channels** 1 行：`{name: "deepseek-main", provider: "deepseek", base_url: "https://api.deepseek.com", api_key_encrypted: "env-injected"（占位，见 research.md D2）, weight: 10, status: "healthy"}`
- **models** 2 行（挂 deepseek-main 渠道，`enabled: true`，价格字段 W3 补）：
  - `deepseek-chat`
  - `deepseek-reasoner`

## 校验规则（W1 生效）

- 入站 `model` 必须 ∈ models 表 `enabled=true` 集合，否则 OpenAI 格式模型不存在错误（Edge Case 2）
- `request_id`（usage_records 唯一索引）：W1 生成但不落库，为 W3 幂等预留格式
- 种子脚本幂等：按 (name, provider) / (model_name) upsert，重复执行无副作用

## 状态迁移（W1 无）

渠道三态熔断（healthy/half-open/open）属 W4；api_keys 状态机属 W2。
