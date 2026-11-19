"""实验库不同数据规模下的执行计划 / 耗时对比（v1.1.2 阶段四）。

任务书要求「对不同数据规模进行执行计划和查询性能测试」，同时明确
「不要直接把 SQLite 执行时间当成真实生产环境的性能指标」。这个脚本：

1. 在临时目录里按 4,000 / 20,000 / 80,000 行分别 ``build_lab``；
2. 对原始查询（DATE(created_at)）与玩家式改写（区间条件）各跑一次真 SQL；
3. 对比 EXPLAIN QUERY PLAN 的判定是否随规模保持不变；
4. 打印本机耗时，但**只作参考**，绝不写回任何生产数值。

用法：py -3 tools/scan_lab_scale.py [--scales 4000,20000,80000]
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lab import replica, sandbox  # noqa: E402
from core.rules import workbench  # noqa: E402

ORIGINAL = next(q["sql"] for q in workbench.catalog() if q["id"] == "q_dashboard")
REWRITE = workbench.REFERENCE_REWRITES["q_dashboard"]


def plan_text(res: dict) -> str:
    return " | ".join(str(p.get("detail", "")) for p in res.get("plan") or []) or res.get("error", "")


def index_names(path: Path) -> set[str]:
    con = sqlite3.connect(path)
    try:
        return {
            r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
            )
        }
    finally:
        con.close()


def scale_report(scales: list[int], workdir: Path | None = None) -> list[dict]:
    """返回每个规模的一行报告；不修改项目里的任何实验库。"""
    rows: list[dict] = []
    with tempfile.TemporaryDirectory() as td:
        base = workdir or Path(td)
        base.mkdir(parents=True, exist_ok=True)
        for n in scales:
            path = base / f"lab_{n}.db"
            replica.build_lab(path, orders_n=n)
            want = {name for name, _, _ in replica.production_indexes()}
            have = index_names(path)
            orig = sandbox.run(ORIGINAL, path=path, prepared=True)
            new = sandbox.run(REWRITE, path=path, prepared=True)
            rows.append({
                "orders": n,
                "indexes_ok": have == want,
                "indexes": ",".join(sorted(have)),
                "orig_used_index": bool(orig.get("used_index")),
                "new_used_index": bool(new.get("used_index")),
                "orig_rows": orig.get("row_count", 0),
                "new_rows": new.get("row_count", 0),
                "orig_ms": orig.get("elapsed_ms", 0),
                "new_ms": new.get("elapsed_ms", 0),
                "orig_plan": plan_text(orig),
                "new_plan": plan_text(new),
            })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scales", default="4000,20000,80000")
    args = ap.parse_args()
    scales = [int(x) for x in args.scales.split(",") if x.strip()]
    rows = scale_report(scales)

    print(f"生产初始索引：{', '.join(name for name, _, _ in replica.production_indexes())}")
    print(f"实验库默认规模：{replica.LAB_ORDERS} 行\n")
    print("| orders | 初始索引对齐 | 原查询用上索引 | 改写用上索引 | 原查询行数 | 改写行数 | 原查询 ms | 改写 ms |")
    print("|---:|:---:|:---:|:---:|---:|---:|---:|---:|")
    for r in rows:
        print(f"| {r['orders']} | {'是' if r['indexes_ok'] else '否'} | "
              f"{r['orig_used_index']} | {r['new_used_index']} | {r['orig_rows']} | "
              f"{r['new_rows']} | {r['orig_ms']} | {r['new_ms']} |")

    problems = []
    for r in rows:
        if not r["indexes_ok"]:
            problems.append(f"{r['orders']} 行：实验库索引与生产 schema 不一致")
        if r["orig_used_index"] or not r["new_used_index"]:
            problems.append(f"{r['orders']} 行：执行计划判定随规模变化了")
        if r["orig_rows"] != r["new_rows"]:
            problems.append(f"{r['orders']} 行：两份查询结果行数不同")

    print("\n结论：EXPLAIN QUERY PLAN 的「是否用上索引」在不同规模下保持一致；"
          "上面毫秒数只是本机 SQLite 的参考值，不参与生产 p95 / 可用性计算。")
    if problems:
        print("PROBLEMS:\n" + "\n".join(problems))
        return 1
    print("SCALE CHECK PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
