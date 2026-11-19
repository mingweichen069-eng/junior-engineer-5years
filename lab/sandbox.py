"""SQL 沙箱：只读、单条 SELECT；另外放行一次性的 CREATE INDEX 用于实验。

CREATE INDEX 会写库，所以只在 tools/scan_load_profile.py 显式调用时才跑，
Web 端一律走 run()，只允许 SELECT。
"""

from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path

from lab import replica

MAX_STEPS = 2_000_000
MAX_ROWS = 100
BLOCKED = (
    "attach", "detach", "pragma", "vacuum", "insert", "update", "delete",
    "drop", "alter", "replace", "load_extension", "reindex",
)
SCAN_BLOCKED = BLOCKED + ("trigger", "view", "table")   # create 交给正则单独校验

INDEX_RE = re.compile(
    r"^create\s+(?:unique\s+)?index\s+(?:if\s+not\s+exists\s+)?"
    r"([a-z_][a-z0-9_]*)\s+on\s+([a-z_][a-z0-9_]*)\s*\(\s*([a-z0-9_,\s]+)\)\s*;?$",
    re.I,
)


class SqlError(Exception):
    pass


def _base_checks(text: str, blocked: tuple[str, ...]) -> None:
    lowered = text.lower()
    for word in blocked:
        if re.search(rf"\b{word}\b", lowered):
            raise SqlError(f"不允许 {word.upper()}")


def validate(sql: str) -> str:
    text = (sql or "").strip()
    if not text:
        raise SqlError("SQL 不能为空")
    if not text.lower().startswith("select"):
        raise SqlError("实验台只允许 SELECT")
    if text.count(";") > 1 or (";" in text[:-1]):
        raise SqlError("一次只能执行一条语句")
    _base_checks(text, BLOCKED)
    return text


def validate_scan(sql: str) -> str:
    """扫描模式：额外允许一条 CREATE INDEX（不能建表、不能建视图）。"""
    text = (sql or "").strip()
    if not INDEX_RE.match(text):
        return validate(text)
    _base_checks(text, SCAN_BLOCKED)
    return text


def _warnings(sql: str, plan: list[tuple]) -> list[str]:
    details = " ".join(str(p[3]) for p in plan)
    out: list[str] = []
    if "SCAN" in details.upper() and "USING INDEX" not in details.upper():
        out.append("全表扫描：没有可用的索引")
    if "TEMP B-TREE" in details.upper():
        out.append("额外排序：ORDER BY 没有用上索引顺序")
    if re.search(r"date\s*\(", sql, re.I):
        out.append("函数包住了索引列（DATE(...)），索引会失效")
    if "user_id" in sql.lower() and "user_id" not in details.lower():
        out.append("user_id 没有索引：只能逐行过滤")
    if "USING INDEX" in details.upper() and "SEARCH" in details.upper():
        out.append("已用上索引")
    if "COVERING INDEX" in details.upper():
        out.append("覆盖索引：不需要回表")
    return out


def used_index(plan: list[tuple]) -> bool:
    """执行计划里是否真的用上了索引。

    两种都算：
    - SEARCH ... USING INDEX（索引查找）
    - ... USING COVERING INDEX（覆盖索引，哪怕整段扫索引也算用上了）
    """
    details = " ".join(str(p[3]) for p in plan).upper()
    if "COVERING INDEX" in details:
        return True
    return "USING INDEX" in details and "SEARCH" in details


def run(sql: str, path: Path | None = None, allow_scan: bool = False) -> dict:
    try:
        text = validate_scan(sql) if allow_scan else validate(sql)
    except SqlError as exc:
        return {"ok": False, "error": str(exc)}
    db = replica.ensure(path)
    if allow_scan:
        conn = sqlite3.connect(db)
    else:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        conn.execute("PRAGMA query_only = ON")
    steps = {"n": 0}

    def handler() -> int:
        steps["n"] += 5000
        return 1 if steps["n"] > MAX_STEPS else 0

    conn.set_progress_handler(handler, 5000)
    started = time.perf_counter()
    try:
        is_create = text.lower().startswith("create")
        if is_create:
            conn.execute(text)          # 先真正建索引
            conn.commit()
            return {
                "ok": True,
                "columns": [],
                "rows": [],
                "row_count": 0,
                "truncated": False,
                "plan": [],
                "used_index": False,
                "warnings": [f"已创建索引：{text}"],
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
            }
        plan = conn.execute("EXPLAIN QUERY PLAN " + text).fetchall()
        cur = conn.execute(text)
        columns = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchmany(MAX_ROWS + 1)
        truncated = len(rows) > MAX_ROWS
        rows = rows[:MAX_ROWS]
        if allow_scan:
            conn.commit()
        elapsed = (time.perf_counter() - started) * 1000
        return {
            "ok": True,
            "columns": columns,
            "rows": [list(r) for r in rows],
            "row_count": len(rows),
            "truncated": truncated,
            "plan": [{"id": p[0], "parent": p[1], "detail": p[3]} for p in plan],
            "used_index": used_index(plan),
            "warnings": _warnings(text, plan),
            "elapsed_ms": round(elapsed, 2),
        }
    except sqlite3.Error as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        conn.close()
