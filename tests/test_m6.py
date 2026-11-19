from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.engine import new_game, step  # noqa: E402
from core.model.state import MonthSnapshot, WeekSnapshot  # noqa: E402
from core.rules import endings  # noqa: E402


def _fake_months(st, availability=0.99, grade="D", n=3):
    st.months = [
        MonthSnapshot(month=i + 1, revenue_cents=0, cost_cents=0, profit_cents=0,
                      cash_cents=st.company.cash_cents, availability=availability,
                      p95_ms=900, grade=grade)
        for i in range(n)
    ]


def _fake_history(st, error_rate=0.01, n=4):
    st.history = [
        WeekSnapshot(week=st.meta.week - i, peak_qps=80, p95_ms=900, error_rate=error_rate,
                     disk_used_gb=1.6, io_util=0.9, cash_cents=st.company.cash_cents)
        for i in range(n)
    ]


def test_healthy_state_is_safe() -> None:
    st = new_game(seed=1)
    score, parts = endings.score(st)
    assert score >= 60
    assert endings.runway_weeks(st) > 26
    assert parts["cash"] == 0


def test_hard_protection_caps_score() -> None:
    st = new_game(seed=1)
    st.org.manager_trust = 0
    _fake_months(st, availability=0.999, grade="D")
    _fake_history(st, error_rate=0.001)
    score, _ = endings.score(st)
    assert score >= 45  # 现金充足 + 稳定 → 不会掉到 warn 以下


def test_layoff_requires_dwell_and_rescue_window() -> None:
    st = new_game(seed=2)
    st.company.cash_cents = -50_000_000
    st.org.manager_trust = 5
    _fake_months(st, availability=0.99, grade="D")
    _fake_history(st, error_rate=0.01)

    stages = []
    for week in range(2, 40):
        st.meta.week = week
        endings.tick(st)
        stages.append(st.ending.stage)
        if st.ending.ended:
            break

    assert st.ending.ended == "layoff"
    assert "watch" in stages and "warn" in stages and "list" in stages
    # 每一档都至少停留 2 周
    assert stages.count("list") >= 2
    assert st.ending.ended_week >= 8


def test_bankrupt_on_two_bad_months() -> None:
    st = new_game(seed=3)
    _fake_months(st, availability=0.9, grade="C")
    _fake_history(st, error_rate=0.1)
    st.meta.week = 20
    st.company.cash_cents = -10_000_00      # 现金见底 + 连续两月不可用
    st.ending.stage = "watch"
    endings.tick(st)
    assert st.ending.ended == "bankrupt"


def test_bankrupt_does_not_preempt_rescue_window() -> None:
    """破产不能抢跑挽救窗口：处于预警时，玩家有 4 周补救。"""
    st = new_game(seed=3)
    st.meta.week = 20
    st.ending.stage = "warn"
    st.ending.rescue_goal = "stability"
    st.ending.rescue_target = 0.995
    st.ending.rescue_deadline = 24
    st.ending.weeks_in_stage = 1
    _fake_months(st, availability=0.9, grade="C")
    _fake_history(st, error_rate=0.1)
    st.company.cash_cents = -10_000_00
    endings.tick(st)
    assert st.ending.ended is None          # 窗口还开着
    st.meta.week = 25                        # 窗口过期
    st.ending.rescue_deadline = 24
    endings.tick(st)
    assert st.ending.stage == "list"         # 进入名单，还要再撑 2 周
    st.meta.week = 26
    endings.tick(st)
    st.meta.week = 27
    endings.tick(st)
    assert st.ending.ended == "layoff"


def test_funded_at_week_24() -> None:
    st = new_game(seed=4)
    st.meta.week = 24
    st.company.registered = 210_000
    st.company.cash_cents = 100_000_00
    for item in st.debt.items.values():
        item.resolved = True
    st.months = [MonthSnapshot(month=6, revenue_cents=0, cost_cents=0, profit_cents=0,
                               cash_cents=st.company.cash_cents, availability=0.999,
                               p95_ms=120, grade="A")]
    _fake_history(st, error_rate=0.001)
    endings.tick(st)
    # v0.9：A 轮资格进履历，但不再强行结束这一局
    assert st.ending.ended is None
    assert any("A 轮资格" in m for m in st.review.milestones)


def test_ended_state_stops_time() -> None:
    st = new_game(seed=5)
    st.ending.ended = "layoff"
    st.ending.ended_week = 10
    week = st.meta.week
    step(st)
    assert st.meta.week == week


def test_stage_has_minimum_dwell() -> None:
    st = new_game(seed=6)
    st.company.cash_cents = -50_000_000
    st.org.manager_trust = 0
    _fake_months(st, availability=0.99, grade="D")
    _fake_history(st, error_rate=0.01)
    st.meta.week = 5
    endings.tick(st)
    assert st.ending.stage == "safe"  # 第一周不跳档
    st.meta.week = 6
    endings.tick(st)
    assert st.ending.stage in {"watch", "safe"}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from services import save as saves

    monkeypatch.setattr(saves, "DB_PATH", tmp_path / "t.db")
    import web.app as webapp

    monkeypatch.setattr(webapp, "CURRENT_SLOT", "t")
    app = webapp.create_app()
    app.testing = True
    return app.test_client()


def test_api_new_game(client) -> None:
    j = client.post("/api/new", json={"seed": 5}).get_json()
    assert j["ok"] and j["data"]["week"] == 1
    assert j["data"]["ending"]["stage"] == "safe"
    assert j["data"]["ending"]["ended"] is None


def test_api_stops_after_ending(client) -> None:
    client.post("/api/new", json={"seed": 3})
    for _ in range(3):
        d = client.post("/api/advance", json={"weeks": 8}).get_json()["data"]
        if d["ending"]["ended"]:
            break
    assert d["ending"]["ended"] in {"bankrupt", "layoff", "funded"}
    week = d["week"]
    d2 = client.post("/api/advance", json={"weeks": 1}).get_json()["data"]
    assert d2["week"] == week
    r = client.post("/api/action", json={"id": "cache_dashboard"}).get_json()
    assert r["ok"] is False and "结束" in r["message"]
