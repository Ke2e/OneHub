"""W4 任务 1：智能路由——加权轮询挑选 + 指数退避重试（含抖动）。

候选 = 同一 model 的多个渠道（models 表非唯一 model_name 复用，Asize 已确认，
零 DDL）。路由时按 channels.weight 加权轮询，跳过不可用（熔断 HALF/OPEN 前
冷却期拒绝或管理员封禁）。失败重试按指数退避 + 随机抖动，避免 thundering herd。

纯函数化便于单测对拍；状态（轮询游标）由 WeightedRobin 实例持有，进程内
（网关单个 lifespan 单例，event-loop 单线程协作调度无线程竞态；跨 worker 由
Redis 熔断状态承载唯一共享依赖）。
"""

import random
import time
from dataclasses import dataclass

# 指数退避默认参数：base 初始延迟、cap 上限、jitter 随机抖动比例
DEFAULT_BASE = 0.1
DEFAULT_CAP = 2.0
DEFAULT_JITTER = 0.15


@dataclass
class Candidate:
    """一条可路由候选：渠道 id + 权重 + 当前是否可用。"""

    channel_id: int
    weight: int = 10
    available: bool = True


class WeightedRobin:
    """加权轮询：游标持续递增，按累计权重区间选中渠道。

    权重均匀轮询——每个请求游标对「可用总权重」取模，落在哪个候选的累计
    权重区间即选中它。weight 大的候选区间更宽 → 被选概率更高，且整体呈
    均匀分布（非纯随机：确定且无长尾扎堆，符合网关负载均衡语义）。
    """

    def __init__(self, rng: random.Random | None = None) -> None:
        self._cursor = 0
        self._rng = rng or random.Random()

    def select(self, candidates: list[Candidate]) -> Candidate | None:
        """从可用候选里加权选一个；无可用候选 → None。"""
        avail = [c for c in candidates if c.available and c.weight > 0]
        if not avail:
            return None
        total = sum(c.weight for c in avail)
        pos = self._cursor % total
        self._cursor += 1
        acc = 0
        for c in avail:
            acc += c.weight
            if pos < acc:
                return c
        return avail[-1]  # 兜底（浮点兜不住也应命中最后一个）


def exponential_backoff(
    attempt: int,
    base: float = DEFAULT_BASE,
    cap: float = DEFAULT_CAP,
    jitter: float = DEFAULT_JITTER,
    rng: random.Random | None = None,
) -> float:
    """指数退避 + 随机抖动：delay = min(cap, base * 2**(attempt-1)) 再乘 (1 ± jitter)。

    attempt 从 1 起（首次重试 attempt=1 → base）。rng 供注入以获得确定性复现
    （同 seed → 同结果），缺省用全局 random。jitter=0 → 纯指数无抖动。
    """
    exp = min(cap, base * (2 ** (attempt - 1)))
    if jitter > 0 and rng is not None:
        factor = 1.0 + (rng.random() * 2 - 1) * jitter
    else:
        factor = 1.0
    return exp * factor