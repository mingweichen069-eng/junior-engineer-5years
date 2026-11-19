"""数据规模、页数、Buffer Pool 命中率。"""

from __future__ import annotations

import math

from core.model.state import Db, Table
from core.rules import constants as C


def table_pages(table: Table) -> int:
    per_page = C.PAGE_BYTES * table.fill_factor / table.row_bytes
    return max(1, math.ceil(table.rows / per_page))


def index_pages(table: Table, index_name: str) -> int:
    idx = table.indexes[index_name]
    if idx.entry_bytes:
        entry = idx.entry_bytes
    else:
        # 入口宽度 = 列宽之和 + 主键 8B + 开销 13B
        widths = {"bigint": 8, "int": 4, "tinyint": 1, "datetime": 5, "decimal": 8, "varchar": 32}
        entry = sum(widths.get(table.columns[c].type, 16) for c in idx.columns) + 8 + 13
    per_page = C.PAGE_BYTES * 0.90 / entry
    eff = table.rows * (1 + idx.bloat)
    return max(1, math.ceil(eff / per_page))


def total_pages(state_db: Db) -> int:
    total = 0
    for table in state_db.tables.values():
        total += table_pages(table)
        for name in table.indexes:
            total += index_pages(table, name)
    return total


def buffer_hit(state_db: Db) -> float:
    pool_pages = state_db.buffer_pool_gb * (1024 ** 3) / C.PAGE_BYTES
    working = total_pages(state_db)
    if working <= 0:
        return C.HIT_MAX
    hit = 1 - math.exp(-pool_pages / (working * C.SKEW))
    return max(C.HIT_MIN, min(C.HIT_MAX, hit))


def disk_used_gb(state_db: Db) -> float:
    return total_pages(state_db) * C.PAGE_BYTES / (1024 ** 3)
