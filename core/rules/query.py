"""查询目录、访问路径枚举与成本计算。"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

from core.model.state import Table
from core.rules import constants as C
from core.rules.storage import index_pages, table_pages

CONTENT = Path(__file__).resolve().parents[2] / "content"


@dataclass(frozen=True)
class Cost:
    rows: int
    pages: int
    io_ops: float
    io_ms: float
    cpu_ms: float
    total_ms: float


@dataclass(frozen=True)
class Path:
    kind: str                 # full_scan | index_eq | index_order | write
    index: str | None
    cost: Cost


def load_catalog(path: Path | None = None) -> list[dict]:
    data = json.loads((path or CONTENT / "queries.json").read_text(encoding="utf-8"))
    return data["queries"]


def is_active(q: dict) -> bool:
    """写查询默认激活：是否参与模拟由 evaluate.effective_query 按世界状态决定。"""
    if q.get("kind") == "write":
        return True
    return bool(q.get("active", True))


def active_queries(catalog: list[dict]) -> list[dict]:
    return [q for q in catalog if is_active(q)]


def cost(rows: int, pages: int, hit: float, *, seq: bool = False,
         index_pages_touched: int = 0, wait_ms: float = 0.0) -> Cost:
    if seq:
        io_ops = pages * (1 - hit)
        io_ms = io_ops * C.C_IO_SEQ_MS + wait_ms
    else:
        io_ops = pages * (1 - hit) + index_pages_touched * (1 - C.INDEX_HIT)
        io_ms = io_ops * (C.C_IO_RAND_MS + wait_ms)
    cpu_ms = (rows * C.C_ROW_US + pages * C.C_PAGE_US) / 1000
    return Cost(
        rows=rows,
        pages=pages,
        io_ops=io_ops,
        io_ms=io_ms,
        cpu_ms=cpu_ms,
        total_ms=C.C_PARSE_MS + io_ms + cpu_ms,
    )


def _selectivity(table: Table, q: dict, column: str) -> float:
    if "selectivity" in q:
        return float(q["selectivity"])
    col = table.columns.get(column)
    if col is None:
        return 1.0
    return min(1.0, 1.0 / max(1, col.cardinality))


def _has_eq(q: dict, column: str) -> bool:
    return any(p["column"] == column and p["op"] == "eq" for p in q.get("predicates", []))


def choose_path(table: Table, q: dict, hit: float, wait_ms: float = 0.0) -> Path:
    """枚举路径取成本最低者，模拟优化器。"""
    if q.get("kind") == "write":
        # 每多一个二级索引，就多一次 B+ 树维护；索引碎片（bloat）会进一步放大
        maintenance = sum(
            C.C_INDEX_INSERT_MS * (1.0 + max(0.0, idx.bloat)) for idx in table.indexes.values()
        )
        base = C.C_WRITE_BASE_MS + maintenance
        io_ms = C.C_IO_RAND_MS + wait_ms
        return Path("write", None, Cost(1, 1, 1.0, io_ms, base, base + io_ms))

    n = table.rows
    p_table = table_pages(table)
    candidates: list[Path] = [
        Path("full_scan", None, cost(n, p_table, hit, seq=True, wait_ms=wait_ms))
    ]

    # 谓词里出现函数（如 DATE(created_at)）时，索引一律不可用
    non_sargable = any(p["op"] == "non_sargable" for p in q.get("predicates", []))

    # 主键等值查询：聚簇索引，1 页直达（不经过二级索引）
    if _has_eq(q, "id"):
        candidates.append(
            Path("pk", None, cost(1, 1, hit, wait_ms=wait_ms))
        )

    if not non_sargable:
        order_by = q.get("order_by") or []
        limit = int(q.get("limit") or 0)
        for name, idx in table.indexes.items():
            lead = idx.columns[0]
            if _has_eq(q, lead):
                sel = _selectivity(table, q, lead)
                rows = max(1, round(n * sel))
                if lead in ("id", "created_at"):
                    # 自增主键 / 时间列：数据物理上有序，范围扫描是聚簇读
                    per_page = max(1.0, C.PAGE_BYTES * table.fill_factor / table.row_bytes)
                    pages = max(1, math.ceil(rows / per_page))
                else:
                    pages = min(rows, p_table)
                ip = max(1, math.ceil(index_pages(table, name) * sel))
                candidates.append(
                    Path("index_eq", name, cost(rows, pages, hit, index_pages_touched=ip, wait_ms=wait_ms))
                )
            elif order_by and lead == order_by[0]:
                pages = min(limit or 20, p_table)
                ip = index_pages(table, name)
                candidates.append(
                    Path("index_order", name, cost(n, pages, hit, index_pages_touched=ip, wait_ms=wait_ms))
                )

    return min(candidates, key=lambda p: p.cost.total_ms)
