"""复现审查报告里的四个致命问题，并验证修复。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.engine import new_game, step  # noqa: E402
from core.model.state import WeekSnapshot  # noqa: E402
from core.rules import endings  # noqa: E402


def _grid(seed: int, weeks: int = 24):
    st = new_game(seed=seed)
    for _ in range(weeks):
        step(st)
        if st.ending.ended:
            break
    return st


def test_reported_incident_no_longer_wipes_users() -> None:
    """原始报告：W9 崩一次，W13 用户从 171,440 掉到 52,375。

    现在：一次事故只影响当月，且流失封顶 12%。
    """
    st = new_game(seed=20261118)
    # 手工构造：W9 崩（90% 错误），W10~W12 正常
    st.history = [
        WeekSnapshot(week=w, peak_qps=78, p95_ms=120,
                     error_rate=(0.9 if w == 9 else 0.0),
                     disk_used_gb=1.6, io_util=0.8, cash_cents=st.company.cash_cents)
        for w in range(6, 13)
    ]
    st.meta.week = 13
    st.company.registered = 171_440
    from core.engine import apply_growth

    for w in range(13, 17):               # 模拟 4 个月末，每月都有一次坏周
        st.history.append(
            WeekSnapshot(week=w, peak_qps=78, p95_ms=120,
                         error_rate=(0.9 if w == 14 else 0.0),
                         disk_used_gb=1.6, io_util=0.8, cash_cents=st.company.cash_cents)
        )
        st.meta.week = w + 1
        apply_growth(st, availability=0.9)
    # 每次事故最多掉 12%，不会出现「一次故障吃掉 70% 用户」
    assert st.company.registered > 120_000


def test_late_game_has_something_to_do() -> None:
    st = new_game(seed=101)
    from core.rules import actions as ar

    catalog = ar.load_actions()
    for week in range(1, 25):
        st.meta.week = week
        st.player.ap = 5
        ready = [a for a in catalog if ar.check(st, a)[0]]
        assert ready, f"第 {week} 周无事可做"


def test_web_state_exposes_skills_and_actions(client) -> None:
    d = client.get("/api/state").get_json()["data"]
    assert "skills" in d and d["skills"]["sql"] >= 0
    rep = [a for a in d["actions"] if a["repeatable"]]
    assert rep, "至少要有可重复动作，否则后期无事可做"


def test_default_run_reaches_an_ending_within_24_weeks() -> None:
    st = _grid(7)
    assert st.ending.ended in {"bankrupt", "layoff", "funded", "unfunded"}
    assert st.meta.week <= 24


def test_idle_player_loses_money_eventually() -> None:
    """不处理大屏：财务一定会在 6 个月内恶化（世界在动）。"""
    st = _grid(3)
    assert st.company.cash_cents < 500_000_00


def test_bankrupt_and_layoff_are_distinguishable() -> None:
    """现金见底 → 技术破产；只是不可用但公司还活着 → 走裁员线。"""
    st = new_game(seed=5)
    st.meta.week = 24
    st.company.cash_cents = -100_000_00
    from core.model.state import MonthSnapshot

    st.months = [
        MonthSnapshot(month=m, revenue_cents=1, cost_cents=1, profit_cents=0,
                      cash_cents=-1, availability=0.9, p95_ms=900, grade="D")
        for m in (5, 6)
    ]
    st.ending.stage = "watch"
    endings.tick(st)
    assert st.ending.ended == "bankrupt"


def test_score_transparent_breakdown() -> None:
    st = new_game(seed=6)
    total, parts = endings.score(st)
    assert set(parts) == {"cash", "availability", "performance", "trust", "wave"}
    assert abs(total - (100 + sum(parts.values()))) < 0.2
