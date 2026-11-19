from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.engine import new_game, step  # noqa: E402
from core.rules import actions as action_rules  # noqa: E402
from core.rules import economy  # noqa: E402
from core.rules.evaluate import evaluate_peak  # noqa: E402


def test_action_requires_ap() -> None:
    st = new_game(seed=1)
    st.player.ap = 0
    ok, why = action_rules.start(st, "cache_dashboard")
    assert not ok and "行动力" in why


def test_action_requires_cash() -> None:
    st = new_game(seed=1)
    st.company.cash_cents = 0
    ok, why = action_rules.start(st, "add_server")
    assert not ok and "现金" in why


def test_cache_reduces_database_load() -> None:
    st = new_game(seed=1)
    before = evaluate_peak(st)
    ok, _ = action_rules.start(st, "cache_dashboard")
    assert ok
    after = evaluate_peak(st)
    dash = after.by_id("q_dashboard")
    assert dash is not None
    assert dash.qps < 0.1
    assert after.io_iops < before.io_iops * 0.3
    assert after.p95_ms < before.p95_ms


def test_index_takes_effect_next_week() -> None:
    st = new_game(seed=1)
    before = evaluate_peak(st).by_id("q_my_orders")
    ok, msg = action_rules.start(st, "add_index_user_created")
    assert ok and "W2" in msg
    assert "idx_orders_user_created" not in st.db.tables["orders"].indexes
    step(st)
    assert "idx_orders_user_created" in st.db.tables["orders"].indexes
    after = evaluate_peak(st).by_id("q_my_orders")
    assert before and after
    assert after.time_ms < before.time_ms / 5


def test_ap_resets_each_week() -> None:
    st = new_game(seed=1)
    cap = action_rules.ap_cap(st)
    assert st.player.ap == cap
    action_rules.start(st, "cache_dashboard")
    assert st.player.ap == cap - action_rules.current_cost(st, {"id": "cache_dashboard", "ap": 3})
    step(st)
    assert st.player.ap == cap


def test_month_report_matches_cash_flow() -> None:
    st = new_game(seed=1)
    start_cash = st.company.cash_cents
    for _ in range(4):
        step(st)
    assert len(st.months) == 1
    m = st.months[0]
    assert m.revenue_cents == sum(w.revenue_cents for w in st.history[:4])
    assert m.cost_cents == sum(w.cost_cents for w in st.history[:4])
    assert st.company.cash_cents - start_cash == m.revenue_cents - m.cost_cents - m.sla_penalty_cents


def test_sla_tiers_are_graduated() -> None:
    # 掉得越深，罚得越重，而不是一刀切
    assert economy.sla_outcome(0.999)[0] == 0
    light = economy.sla_outcome(0.992)[0]
    heavy = economy.sla_outcome(0.985)[0]
    worst = economy.sla_outcome(0.95)[0]
    assert 0 < light < heavy < worst
    assert economy.sla_outcome(0.95)[1] < economy.sla_outcome(0.992)[1]   # 信任跌更多


def test_sla_penalty_applied() -> None:
    st = new_game(seed=1)
    for _ in range(4):
        step(st)
    cash_before = st.company.cash_cents
    trust_before = st.org.manager_trust
    snap = economy.settle_month(st, availability=0.95, p95_ms=900)
    assert snap.sla_penalty_cents == economy.sla_outcome(0.95)[0] * 100
    assert st.company.cash_cents == cash_before - snap.sla_penalty_cents
    assert st.org.manager_trust < trust_before


def test_veteran_can_rewrite_dashboard(exam_pass) -> None:
    st = new_game(seed=1, difficulty="veteran")
    assert exam_pass(st, "db_t2")[0]
    ok, _ = action_rules.start(st, "rewrite_dashboard")
    assert ok
    step(st)
    m = evaluate_peak(st)
    dash = m.by_id("q_dashboard")
    assert dash is not None
    assert dash.time_ms < 20
    assert dash.rows < 5000
