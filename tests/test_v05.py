"""v0.5：考核判定、难度分档、结局补齐、只读副本。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.engine import new_game, step  # noqa: E402
from core.model.state import MonthSnapshot  # noqa: E402
from core.rules import difficulty, endings  # noqa: E402
from core.rules.evaluate import evaluate_peak  # noqa: E402


# ---------- P0-1：覆盖索引也算用上索引 ----------

def test_sandbox_recognises_covering_index(tmp_path) -> None:
    from lab import replica, sandbox

    db = tmp_path / "cover.db"
    replica.build(db)
    assert sandbox.run(
        "CREATE INDEX idx_cover ON orders(user_id, created_at)", db, allow_scan=True
    )["ok"]
    # 整段扫索引（SCAN + COVERING INDEX），没有 SEARCH 关键字——旧逻辑会判失败
    r = sandbox.run(
        "SELECT user_id, created_at FROM orders ORDER BY user_id, created_at LIMIT 5", db
    )
    assert r["ok"]
    plan = " ".join(p["detail"].upper() for p in r["plan"])
    assert "COVERING INDEX" in plan and "SEARCH" not in plan
    assert r["used_index"] is True
    assert any("覆盖索引" in w for w in r["warnings"])


def test_exam_judging_uses_real_database() -> None:
    """考核不是选择题：SQL 真的跑，执行计划真的判。"""
    from core.rules import exam as exam_rules
    from core.rules import tech as tech_rules

    node = tech_rules.node_def(tech_rules.load(), "db_t2")
    task = exam_rules.task_for(node)
    ok, msg, detail = exam_rules.validate(task, "SELECT COUNT(*) FROM orders WHERE DATE(created_at) = '2023-12-01'")
    assert not ok and "date(" in msg.lower()
    ok, msg, detail = exam_rules.validate(task, exam_rules.solution(node))
    assert ok, msg
    assert detail["plan"]


# ---------- P1-1：四档难度独立参数 ----------

def test_difficulty_tiers_are_distinct() -> None:
    easy = new_game(seed=5, difficulty="easy")
    normal = new_game(seed=5, difficulty="normal")
    hard = new_game(seed=5, difficulty="hard")
    veteran = new_game(seed=5, difficulty="veteran")

    assert easy.company.cash_cents > normal.company.cash_cents > hard.company.cash_cents
    assert easy.meta.seed != hard.meta.seed                # 同 seed 不同局
    assert difficulty.config("easy")["event_density"] < difficulty.config("hard")["event_density"]
    assert difficulty.config("easy")["availability_redline"] < difficulty.config("hard")["availability_redline"]


def test_hard_redline_makes_bankruptcy_easier() -> None:
    def two_months(avail: float, diff: str):
        st = new_game(seed=6, difficulty=diff)
        st.months = [
            MonthSnapshot(month=m, revenue_cents=0, cost_cents=0, profit_cents=0,
                          cash_cents=st.company.cash_cents, availability=avail, p95_ms=900, grade="C")
            for m in (1, 2)
        ]
        return endings._bankrupt(st)

    assert two_months(0.982, "normal") is False
    assert two_months(0.982, "hard") is True


def test_rules_version_is_current() -> None:
    st = new_game(seed=1)
    assert st.meta.rules_version == "1.0.0"
    assert st.meta.save_version >= 7


# ---------- P2-1：健康但融资失败有独立结局 ----------

def test_unfunded_ending_at_week_24() -> None:
    st = new_game(seed=12)
    st.meta.week = 24
    st.company.registered = 170_000          # 增长不达标 → 拿不到 A 轮
    st.company.cash_cents = 100_000_00
    for item in st.debt.items.values():
        item.resolved = True
    st.months = [
        MonthSnapshot(month=m, revenue_cents=0, cost_cents=0, profit_cents=0,
                      cash_cents=st.company.cash_cents, availability=0.998, p95_ms=120, grade="A")
        for m in (4, 5, 6)
    ]
    st.history = [
        __import__("core.model.state", fromlist=["WeekSnapshot"]).WeekSnapshot(
            week=21 + i, peak_qps=78, p95_ms=120, error_rate=0.002,
            disk_used_gb=1.6, io_util=0.7, cash_cents=st.company.cash_cents)
        for i in range(4)
    ]
    st.ending.stage = "safe"
    endings.tick(st)
    # v0.9：系统健康但没融资不再强行结束，只记一条履历；游戏继续到角色失败
    assert st.ending.ended is None
    assert any("融资窗口" in m for m in st.review.milestones)
    assert st.meta.review_week == 60


# ---------- P2-2：只读副本 ----------

