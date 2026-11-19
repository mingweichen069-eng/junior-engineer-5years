from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.engine import new_game, step  # noqa: E402
from core.model.serde import from_jsonable, to_jsonable  # noqa: E402
from core.model.state import GameState  # noqa: E402
from core.rules import actions as action_rules  # noqa: E402
from core.rules import career, debt as debt_rules, events as event_rules  # noqa: E402
from core.rules.evaluate import evaluate_peak  # noqa: E402

import pytest


@pytest.fixture(autouse=True)
def _legacy_event_density(monkeypatch):
    """本文件测的是因果链 / 冷却 / 日志持久化，不是 v0.9 的事件间隔。"""
    from core.rules import difficulty
    monkeypatch.setitem(difficulty.CONFIGS["normal"], "event_density", 1.0)
    monkeypatch.setitem(difficulty.CONFIGS["normal"], "event_gap_weeks", 0)


def test_dashboard_event_forms_a_chain() -> None:
    st = new_game(seed=42)
    step(st)
    log = st.events.log
    assert len(log) >= 2
    root = log[0]
    assert root.event_id == "E01_dashboard_slow"
    child = log[1]
    assert child.cause_id == root.log_id
    assert child.depth == 1
    assert child.day >= root.day


def test_event_cooldown_blocks_next_week() -> None:
    st = new_game(seed=42)
    step(st)
    fired_week = st.meta.week
    assert any(e.event_id == "E01_dashboard_slow" for e in st.events.log)
    step(st)
    later = [e for e in st.events.log if e.week == fired_week + 1 and e.event_id == "E01_dashboard_slow"]
    assert not later


def test_chain_is_bounded_over_30_weeks() -> None:
    st = new_game(seed=7)
    seen: dict[int, list] = {}
    for _ in range(30):
        step(st)
        seen.setdefault(st.meta.week, []).append(None)
    per_week: dict[int, int] = {}
    for e in st.events.log:
        per_week[e.week] = per_week.get(e.week, 0) + 1
        assert e.depth <= event_rules.MAX_DEPTH
    assert all(n <= event_rules.MAX_EVENTS_PER_WEEK for n in per_week.values())
    assert len(st.events.log) > 0


def test_collapse_event_adds_errors() -> None:
    st = new_game(seed=3)
    metrics = evaluate_peak(st)
    metrics.io_util = 0.99
    impact = event_rules.run_week(st, metrics)
    assert "E02_peak_collapse" in impact.fired
    assert impact.error_add > 0
    assert st.org.tickets > 0


def test_debt_grows_with_load() -> None:
    st = new_game(seed=1)
    before = st.debt.items["nonsargable_daily_report"].severity
    step(st)
    after = st.debt.items["nonsargable_daily_report"].severity
    assert after > before


def test_monitoring_resolves_debt() -> None:
    st = new_game(seed=1)
    ok, _ = action_rules.start(st, "open_monitoring")
    assert ok
    step(st)  # W2：监控生效
    assert st.debt.items["no_monitoring"].resolved
    assert st.actions.monitoring


def test_org_and_grade() -> None:
    st = new_game(seed=1)
    trust_before = st.org.manager_trust
    for _ in range(5):
        step(st)
    assert st.org.manager_trust < trust_before
    assert st.months and st.months[0].grade in {"S", "A", "B", "C", "D"}
    assert 0 <= career.performance_score(st, 0.999) <= 100
    assert 0 <= debt_rules.pressure(st) <= 1


def test_event_log_survives_save_roundtrip() -> None:
    st = new_game(seed=9)
    step(st)
    restored = from_jsonable(GameState, to_jsonable(st))
    assert len(restored.events.log) == len(st.events.log)
    assert restored.events.log[0].log_id == st.events.log[0].log_id
