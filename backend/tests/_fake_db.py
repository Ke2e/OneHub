"""离线桩 AsyncSession（W2 管理面测试专用，延续 T022 桩风格）。

支持管理面端点所需的写路径：add / commit / flush / refresh + select 过滤查询。
不加真实 SQL 语义：whereclause 仅解析 `列 == 值`（含 and_ 组合），按对象属性匹配。

用法：monkeypatch 目标管理路由模块的 AsyncSession 为该工厂。
注意：模块名带下划线开头（_fake_db）——非测试文件，不参与 pytest 收集。
"""

from collections import defaultdict
from typing import Any

from sqlalchemy import false, true
from sqlalchemy.sql.elements import BinaryExpression
from sqlalchemy.sql.operators import is_ as _is_op
from sqlalchemy.sql.operators import isnot as _isnot_op


class FakeScalars:
    """模拟 AsyncScalarResult：仅支持 .all()（对齐 T022 桩）。"""

    def __init__(self, rows: list):
        self._rows = rows

    def all(self):
        return self._rows


def _col_and_value(binary: BinaryExpression) -> tuple[str, Any]:
    """从 `列 == 值` / `列.is_(布尔)` 二元表达式提取 (列名, 值)。

    SQLAlchemy 2.0 中右值以 BindParameter 形式出现（执行期才绑定），
    取值需取 .value（无则回退为字面量本身）。
    `is_(True)` 类条件右值是 true()/false() 常量（无 .value），
    按 operator 判定为 is_/isnot 时转成 bool。
    """
    name = getattr(binary.left, "key", None) or getattr(binary.left, "name", None)
    if name is None:
        raise ValueError(f"cannot parse where clause: {binary}")
    if binary.operator is _is_op or binary.operator is _isnot_op:
        # true()/false() 为模块级单例（elements.TRUE/FALSE）；bool() 求值会被禁
        value = binary.right is true()
        if binary.operator is _isnot_op:
            value = not value
        return name, value
    return name, getattr(binary.right, "value", binary.right)


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

    def delete(self, obj):
        """硬删：从 store 移除（models 硬删用，usage_records.model 为 varchar 非 FK）。"""
        if obj in self._store[type(obj)]:
            self._store[type(obj)].remove(obj)

    # ── 读路径 ──
    def _store_key(self, stmt):
        """定位 stmt 的目标实体类型。

        select(User) 的 raw column 是 AnnotatedTable（User.__table__）而非实体类，
        按 __tablename__ 反查 store 的实体类型键。
        """
        try:
            raw = stmt._raw_columns[0]
        except (AttributeError, TypeError):
            return None
        if isinstance(raw, type):
            return raw
        name = getattr(raw, "name", None)
        for cls in self._store:
            if getattr(cls, "__tablename__", None) == name:
                return cls
        return None

    def _query(self, stmt):
        filters = _filters(stmt)
        target = self._store.get(self._store_key(stmt), [])
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

    async def get(self, model_type: type, pk):
        """按主键直取（对齐 AsyncSession.get）。"""
        for obj in self._store.get(model_type, []):
            if getattr(obj, "id", None) == pk:
                return obj
        return None

    # ── 测试断言辅助 ──
    def stored(self, model_type: type) -> list:
        return list(self._store[model_type])