"""平衡扫描：5 种策略 × N 个种子 × 24 周。

用法：py -3 tools/balance.py --seeds 20 --weeks 24
"""

from __future__ import annotations

import argparse
import statistics
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.engine import new_game, step  # noqa: E402
from core.rules import actions as action_rules  # noqa: E402
from core.rules import workbench as workbench_rules  # noqa: E402

CATALOG = action_rules.load_actions()


def try_act(st, action_id: str) -> bool:
    ok, _ = action_rules.start(st, action_id, CATALOG)
    return ok


def s_idle(st, week: int) -> None:
    return


def s_index(st, week: int) -> None:
    if week == 1:
        try_act(st, "add_index_user_created")

def s_cache(st, week: int) -> None:
    if week == 1:
        try_act(st, "cache_dashboard")


def s_scale(st, week: int) -> None:
    if week in (1, 3, 5, 7) and st.company.cash_cents > 0:
        try_act(st, "add_server")


def deploy(st, node_id: str) -> None:
    """模拟一个真的会动手的玩家：在工作台把技术部署成公司资产。"""
    if node_id == "db_t2":
        workbench_rules.deploy_rewrite(st, "q_dashboard",
                                       workbench_rules.REFERENCE_REWRITES["q_dashboard"])
        workbench_rules.deploy_index(st, "CREATE INDEX ix_bal_created ON orders(created_at)")
    else:
        workbench_rules.deploy_index(st, "CREATE INDEX ix_bal_user ON orders(user_id, created_at)")


def s_balanced(st, week: int) -> None:
    """均衡线：监控→缓存→索引→工作台实验与部署→改写→运维债→扩容。"""
    plan = {
        1: "open_monitoring",
        2: "cache_dashboard",
        3: "add_index_user_created",
        7: "rewrite_dashboard",
        9: "circuit_breaker",
        10: "backup_daily",
        11: "write_docs",
    }
    # v1.0：没有读条与考核，改成「真的把索引 / 查询改写部署到生产」
    if week == 5:
        deploy(st, "db_t1")
        deploy(st, "db_t2")
    if week in plan:
        try_act(st, plan[week])
    if week in (9, 11, 13) and st.company.cash_cents > 0:
        try_act(st, "add_server")


STRATEGIES = {
    "S1 救火型": s_idle,
    "S2 索引狂": s_index,
    "S3 缓存党": s_cache,
    "S4 扩容党": s_scale,
    "S5 均衡型": s_balanced,
}


def run(strategy, seeds: int, weeks: int) -> dict:
    outcomes: Counter = Counter()
    cashes, avails, grades = [], [], []
    for seed in range(1, seeds + 1):
        st = new_game(seed=seed)
        for week in range(1, weeks + 1):
            strategy(st, week)
            if st.ending.ended:
                break
            step(st)
        outcomes[st.ending.ended or "未结束"] += 1
        cashes.append(st.company.cash_cents / 100)
        if st.months:
            avails.append(st.months[-1].availability * 100)
            grades.append(st.months[-1].grade)
        else:
            avails.append(100.0)
    return {
        "outcomes": outcomes,
        "cash": statistics.mean(cashes),
        "avail": statistics.mean(avails),
        "grades": Counter(grades),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", type=int, default=20)
    parser.add_argument("--weeks", type=int, default=24)
    args = parser.parse_args()

    print(f"策略 × {args.seeds} 种子 × {args.weeks} 周\n")
    header = (
        f"{'策略':<10}{'A轮':>5}{'融资失败':>8}{'裁员':>5}{'破产':>5}{'未结束':>6}"
        f"{'平均现金':>12}{'平均可用性':>10}"
    )
    print(header)
    print("-" * len(header))
    results = {}
    for name, fn in STRATEGIES.items():
        r = run(fn, args.seeds, args.weeks)
        results[name] = r
        o = r["outcomes"]
        print(
            f"{name:<10}{o.get('funded', 0):>5}{o.get('unfunded', 0):>8}"
            f"{o.get('layoff', 0):>5}{o.get('bankrupt', 0):>5}"
            f"{o.get('未结束', 0):>6}{r['cash']:>12,.0f}{r['avail']:>9.2f}%"
        )
        print(f"           绩效分布 {dict(r['grades'])}")


if __name__ == "__main__":
    main()
