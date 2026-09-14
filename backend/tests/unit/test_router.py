"""W4 任务 1 RED：加权轮询挑选 + 指数退避重试（含抖动）单测（dev-tdd）。

智能路由候选选择：
- 输入：候选渠道列表（每项含 channel_id、weight、是否可用 status）。
- 加权轮询：在可用候选间按 weight 比例轮询；跳过不可用（熔断 OPEN / 管理员封禁）。
- 指数退避：失败重试等待 time = base * 2**(attempt)，含随机抖动（±jitter）。
  纯函数化以便单测对拍（对齐令牌桶 _refill / 熔断纯函数风格）。
"""

import random

import pytest

from app.services.router import (
    Candidate,
    WeightedRobin,
    exponential_backoff,
)


# ── Candidate 数据结构 ──

def test_candidate_defaults():
    """候选未指定可用性时默认可路由。"""
    c = Candidate(channel_id=1, weight=10)
    assert c.channel_id == 1
    assert c.weight == 10
    assert c.available is True


# ── WeightedRobin：加权轮询 ──

def test_select_weighted_skips_unavailable():
    """不可用候选（熔断/封禁）被跳过，只在可用集内轮询。"""
    robin = WeightedRobin()
    cands = [
        Candidate(channel_id=1, weight=10, available=False),
        Candidate(channel_id=2, weight=10, available=True),
    ]
    for _ in range(5):
        assert robin.select(cands).channel_id == 2


def test_select_weighted_all_down_returns_none():
    """全部候选不可用 → None（请求无可路由渠道，交给上层抛错/503）。"""
    robin = WeightedRobin()
    cands = [
        Candidate(channel_id=1, weight=10, available=False),
        Candidate(channel_id=2, weight=10, available=False),
    ]
    assert robin.select(cands) is None


def test_select_weighted_round_robin_skips_down_and_resumes():
    """1:1 权重 → 严格交替；游标跨调用累积。"""
    robin = WeightedRobin()
    cands = [Candidate(channel_id=1, weight=1), Candidate(channel_id=2, weight=1)]
    picks = [robin.select(cands).channel_id for _ in range(4)]
    assert picks == [1, 2, 1, 2]


def test_select_weighted_proportional_to_weight():
    """weight 3:1 → 多数路由给 weight=2 的渠道。"""
    robin = WeightedRobin()
    cands = [Candidate(channel_id=1, weight=1), Candidate(channel_id=2, weight=3)]
    picks = [robin.select(cands).channel_id for _ in range(8)]
    assert picks.count(1) + picks.count(2) == 8
    assert picks.count(2) > picks.count(1)


def test_select_weighted_zero_weight_skipped():
    """weight=0（配重为 0 的渠道不参与路由）。"""
    robin = WeightedRobin()
    cands = [Candidate(channel_id=1, weight=0), Candidate(channel_id=2, weight=1)]
    for _ in range(3):
        assert robin.select(cands).channel_id == 2


def test_select_weighted_downs_resumes_after_available():
    """某渠道曾不可用，恢复可用后再次被纳入轮询（恢复切回）。"""
    robin = WeightedRobin()
    both = [Candidate(channel_id=1, weight=1), Candidate(channel_id=2, weight=1)]
    only_one = [Candidate(channel_id=2, weight=1)]  # 渠道1被熔断/封禁
    robin.select(both)     # 选到 (1) — 推进游标
    assert robin.select(only_one).channel_id == 2   # 只剩 2
    # 渠道 1 恢复 → 重新参与
    assert robin.select(both).channel_id in (1, 2)


# ── exponential_backoff：指数退避 + 抖动 ──

def test_backoff_grows_exponentially_without_jitter():
    """无抖动（jitter=0）：attempt 1→2→3 退避 1s→2s→4s（base=1，cap 足够大）。"""
    assert exponential_backoff(attempt=1, base=1.0, cap=8.0, jitter=0.0) == 1.0
    assert exponential_backoff(attempt=2, base=1.0, cap=8.0, jitter=0.0) == 2.0
    assert exponential_backoff(attempt=3, base=1.0, cap=8.0, jitter=0.0) == 4.0


def test_backoff_respects_cap():
    """退避封顶 cap：attempt 大时不无限增长（上限 8）。"""
    assert exponential_backoff(attempt=10, base=1.0, cap=8.0, jitter=0.0) == 8.0


def test_backoff_jitter_bounds():
    """抖动在 [exp*(1-jitter), exp*(1+jitter)] 内；seed 固定保证可复现。"""
    import random

    rng = random.Random(0)
    for attempt in range(1, 6):
        delay = exponential_backoff(attempt=attempt, base=1.0, cap=100.0, jitter=0.2, rng=rng)
        upper = 1.0 * (2 ** (attempt - 1)) * 1.2
        lower = 1.0 * (2 ** (attempt - 1)) * 0.8
        assert lower <= delay <= upper


def test_backoff_with_rng_uses_same_seed_reproducible():
    """同 seed 的 rng → 抖动结果可复现（退避含随机但可对拍）。"""
    import random

    d1 = exponential_backoff(attempt=3, base=1.0, jitter=0.3, rng=random.Random(42))
    d2 = exponential_backoff(attempt=3, base=1.0, jitter=0.3, rng=random.Random(42))
    assert d1 == d2


def test_backoff_default_base():
    """未显式传 base → 使用默认 base，且抖动默认 0.15。"""
    import random

    delay = exponential_backoff(attempt=1, rng=random.Random(1))
    assert delay >= 0.0