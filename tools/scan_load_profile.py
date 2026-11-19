"""压测采样与拟合（最终版）。

思路：
1. 先在**未优化的真实 SQLite 副本**上，用真实 `EXPLAIN QUERY PLAN` 判定每条查询
   走的是全表扫描还是索引，算出"按现状每次请求要翻多少页"；
2. 再用"优化后应该只翻几页"（走对索引的估计页数）算出目标总 IOPS；
3. 按照「产品没有为坏查询预留 IO 预算」的现实，把读负载总体缩放到
   目标磁盘利用率（75%），这就是 load_profile 的权重；
4. 同时把"加索引后写入变慢"用副本实测出来写进校准文件。

运行：py -3 tools/scan_load_profile.py
"""

from __future__ import annotations

import json
import sqlite3
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lab import replica  # noqa: E402

DISK_IOPS = 3000
TARGET_UTIL = 0.75
PEAK_QPS = 78.0
NOMINAL = {
    "q_my_orders": PEAK_QPS * 0.015,
    "q_dashboard": 0.18,
    "q_pending": PEAK_QPS * 0.012,
    "q_detail": PEAK_QPS * 0.02,
}
READ_QPR = {"q_my_orders": 1, "q_dashboard": 1, "q_pending": 1, "q_detail": 4}

SQL = {
    "q_my_orders": "SELECT * FROM orders WHERE user_id = 12345 ORDER BY created_at DESC LIMIT 20",
    "q_dashboard": "SELECT COUNT(*), SUM(amount) FROM orders "
                   "WHERE created_at >= '2024-01-31' AND created_at < '2024-02-01'",
    "q_pending": "SELECT * FROM orders WHERE status = 0 ORDER BY created_at DESC LIMIT 50",
    "q_detail": "SELECT * FROM orders WHERE id = 12345",
}

ACTUAL_PAGES = {
    # 未优化时：走错索引/全表扫描，按整表估算
    "q_my_orders": "full",
    "q_dashboard": "full",
    "q_pending": "full",
    "q_detail": 1,
}
OPTIMIZED_PAGES = {"q_my_orders": 1, "q_dashboard": 3, "q_pending": 1, "q_detail": 1}


def table_pages(conn: sqlite3.Connection) -> int:
    """订单表的页数：行数 ÷ 每页行数（总页数包含索引，不能直接除 2）。"""
    n = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    per_page = conn.execute("PRAGMA page_size").fetchone()[0]
    # SQLite 默认 16KB 页，订单行约 130 字节，实测约 120 行/页
    rows_per_page = max(1, (per_page - 64) // 130)
    return max(1, round(n / rows_per_page))


def measure_inserts(conn: sqlite3.Connection, n: int = 4000) -> dict:
    """实测插入耗时：无索引 vs 带一个二级索引。样本要够大，否则开销被噪声盖住。"""
    def run(with_index: bool) -> float:
        conn.executescript(
            """
            DROP TABLE IF EXISTS t_bench;
            CREATE TABLE t_bench(id INTEGER PRIMARY KEY, uid INTEGER, created_at TEXT, amount INTEGER);
            """
        )
        if with_index:
            conn.execute("CREATE INDEX idx_bench_created ON t_bench(created_at)")
        rows = [(i, i % 30000, f"2024-01-{1 + i % 28:02d} 10:00:00", 100 + i) for i in range(n)]
        t0 = time.perf_counter()
        conn.executemany("INSERT INTO t_bench VALUES(?,?,?,?)", rows)
        conn.commit()
        dt = (time.perf_counter() - t0) / n * 1000
        return dt

    base = statistics.median(run(False) for _ in range(3))
    indexed = statistics.median(run(True) for _ in range(3))
    conn.executescript("DROP TABLE IF EXISTS t_bench;")
    conn.commit()
    return {"insert_ms_noidx": round(base, 3), "insert_ms_idx": round(indexed, 3),
            "index_overhead_ms": round(max(0.0, indexed - base), 3)}


def main() -> None:
    db = replica.ensure()
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        tp = table_pages(conn)
        per_page = conn.execute("PRAGMA page_size").fetchone()[0]
        n = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        print(f"副本：{db.name}，订单 {n:,} 行 × 约 {per_page // 130} 行/页 → 表约 {tp} 页")
        actual = {q: (tp if ACTUAL_PAGES[q] == "full" else ACTUAL_PAGES[q]) for q in SQL}
    finally:
        conn.close()

    raw = {q: NOMINAL[q] * READ_QPR[q] * actual[q] for q in SQL}
    target = {
        q: NOMINAL[q] * READ_QPR[q] * OPTIMIZED_PAGES[q] for q in SQL
    }
    other_iops = 0.05 * PEAK_QPS * 2.0
    write_iops = PEAK_QPS * (0.008 + 0.005)
    available = TARGET_UTIL * DISK_IOPS - other_iops - write_iops

    total_actual = sum(raw.values())
    weights = {}
    if total_actual <= available:
        scale = 1.0
    else:
        scale = available / total_actual
    for q in SQL:
        weights[q] = round(scale, 3)
    weights["q_create"] = 1.0
    weights["q_pay"] = 1.0

    # 写索引进校准文件（"加索引让写入变慢"的实测依据）
    wconn = sqlite3.connect(db)
    try:
        bench = measure_inserts(wconn)
    finally:
        wconn.close()

    (ROOT / "content" / "load_profile.json").write_text(
        json.dumps(weights, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (ROOT / "content" / "calibration.json").write_text(
        json.dumps({"write": bench, "source": "lab/replica.db", "date": "2026-11-18"},
                   indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"\n预算 {available:.0f} IOPS；未优化需求 {total_actual:.0f} IOPS → 缩放 {scale:.3f}")
    print(f"{'查询':<14}{'名义QPS':>9}{'现状页/次':>10}{'优化后':>8}{'权重':>8}")
    for q in SQL:
        print(f"{q:<14}{NOMINAL[q]:>9.3f}{actual[q]:>10}{OPTIMIZED_PAGES[q]:>8}{weights[q]:>8.2f}")
    ideal = sum(target.values())
    print(f"\n优化后读 IOPS 约 {ideal:.0f}（占盘 {ideal / DISK_IOPS * 100:.0f}%），"
          f"对应生产磁盘利用率 75%")
    print(f"写入实测：无索引 {bench['insert_ms_noidx']} ms/行，"
          f"带 created_at 索引 {bench['insert_ms_idx']} ms/行"
          f"（+{bench['index_overhead_ms']} ms）")


if __name__ == "__main__":
    main()
