"""离线桩 AsyncSession（W2 管理面测试专用，延续 T022 桩风格）。

支持管理面端点所需的写路径：add / commit / flush / refresh + select 过滤查询。
不加真实 SQL 语义：whereclause 仅解析 `列 == 值`（含 and_ 组合），按对象属性匹配。

用法：monkeypatch 目标管理路由模块的 AsyncSession 为该工厂。
注意：模块名带下划线开头（_fake_db）——非测试文件，不参与 pytest 收集。
"""

from collections import defaultdict
from typing import Any

from sqlalchemy.sql.elements import BinaryExpression


class FakeScalars:
    """模拟 AsyncScalarResult：仅支持 .all()（对齐 T022 桩）。"""

    def __init__(self, rows: list):
        self._rows = rows

    def all(self):
        return self._rows


def _col_and_value(binary: BinaryExpression) -> tuple[str, Any]:
    """从 `列 == 值` 二元表达式提取 (列名, 值)。"""
    name = getattr(binary.left, "key", None) or getattr(binary.left, "name", None)
    if name is None:
        raise ValueError(f"cannot parse where clause: {binary}")
    return name, binary.right


def _filters(stmt) -> list[tuple[str, Any]]:
    """提取 wherecle 的 [(列, 值)] 列表；支持单条件与 and_ 组合。"""
    wc = stmt.whereclause
    if wc is None:
        return []
    if hasattr(wc, "left"):  # 单 BinaryExpression
        return [_col_and_value(wc)]
    return [
        _col_and_value(c) for c in wc.get_children() if isinstance(c, BinaryExpression)
    ]


class FakeSession:
    """可变状态桩：add 进按类型的内部 store，commit 分配自增 id，查询按列值过滤。"""

    def __init__(self, initial=None):
        self._store: dict[type, list] = defaultdict(list)
        for obj in initial or []:
            self.add(obj)

    # ── 上下文 ──
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    # ── 写路径 ──
    def add(self, obj):
        self._store[type(obj)].append(obj)

    async def commit(self):
        for objs in self._store.values():
            for idx, obj in enumerate(objs, start=1):
                if getattr(obj, "id", None) is None:
                    obj.id = idx

    async def flush(self):
        await self.commit()

    async def refresh(self, obj):
        pass  # id 已在 commit 分配

    # ── 读路径 ──
    def _query(self, stmt):
        filters = _filters(stmt)
        try:
            # select(User) → 首个 raw column 即实体类；失败则遍历全部类型兜底
            target = self._store.get(stmt._raw_columns[0], [])
        except (AttributeError, TypeError):
            target = [obj for objs in self._store.values() for obj in objs]
        return [
            obj
            for obj in target
            if all(getattr(obj, name, None) == value for name, value in filters)
        ]

    async def scalars(self, stmt):
        return FakeScalars(self._query(stmt))

    async def scalar(self, stmt):
        rows = self._query(stmt)
        return rows[0] if rows else None

    # ── 测试断言辅助 ──
    def stored(self, model_type: type) -> list:
        return list(self._store[model_type])