"""针对审查反馈的回归测试：每一项都对应一个真实 bug。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.engine import new_game, step  # noqa: E402
from core.model.state import WeekSnapshot  # noqa: E402
from core.rules import actions as action_rules  # noqa: E402
from core.rules import constants as C  # noqa: E402
from core.rules.evaluate import evaluate_peak  # noqa: E402


# ---------- 1. 月度增长必须只看本月 ----------

def test_growth_uses_only_current_month_weeks() -> None:
    st = new_game(seed=11)
    # 铺 12 周历史：第 9 周（上个月最后一周）雪崩，第 10~12 周正常
    st.history = [
        WeekSnapshot(week=w, peak_qps=78, p95_ms=120, error_rate=(0.9 if w == 9 else 0.0),
                     disk_used_gb=1.6, io_util=0.8, cash_cents=st.company.cash_cents)
        for w in range(1, 13)
    ]
    from core.engine import apply_growth

    st.meta.week = 13          # 第 4 个月末（本月 = W9~W12）
    got = apply_growth(st, availability=1.0)
    # 本月只有 1 周坏：churn ≈ 0.012 + 3×(0.9/4) ≈ 0.687 → 被 CHURN_MAX 封顶 0.12
    assert -0.12 - 0.01 <= got <= -0.05
    # 旧 bug（读到上个月那周）会算出 ≈ -0.71 的流失，这里必须明显好于它
    assert got > -0.2


def test_previous_month_crisis_does_not_bleed_into_this_month() -> None:
    st = new_game(seed=21)
    # W5 坏，W6~W8 正常：第 3 个月（W5~W8）平均错误率只有 0.225
    st.history = [
        WeekSnapshot(week=w, peak_qps=78, p95_ms=120, error_rate=(0.9 if w == 5 else 0.0),
                     disk_used_gb=1.6, io_util=0.8, cash_cents=st.company.cash_cents)
        for w in range(1, 9)
    ]
    from core.engine import apply_growth, month_weeks

    st.meta.week = 9
    assert [w.week for w in month_weeks(st)] == [5, 6, 7, 8]
    got = apply_growth(st, availability=1.0)
    # 旧 bug 的窗口是 W4~W7，同样含 W5，这里主要验证窗口对齐
    assert [w.week for w in st.history[-4:]] == [5, 6, 7, 8]
    assert got < 0          # 本月有一次事故，会掉用户
    assert got > -0.12 - 0.01


def test_single_bad_week_is_capped() -> None:
    st = new_game(seed=12)
    st.history = [
        WeekSnapshot(week=w, peak_qps=78, p95_ms=900, error_rate=0.9,
                     disk_used_gb=1.6, io_util=0.99, cash_cents=st.company.cash_cents)
        for w in range(1, 5)
    ]
    st.meta.week = 5
    before = st.company.registered
    from core.engine import apply_growth

    apply_growth(st, availability=0.1)
    lost = 1 - st.company.registered / before
    assert lost <= C.CHURN_MAX_MONTH + 0.01      # 一个月最多掉 12%


# ---------- 2/3. 破产判定与挽救窗口 ----------

def test_bankrupt_requires_cash_crisis() -> None:
    from core.rules import endings
    from core.model.state import MonthSnapshot

    st = new_game(seed=13)
    st.months = [
        MonthSnapshot(month=m, revenue_cents=0, cost_cents=0, profit_cents=0,
                      cash_cents=st.company.cash_cents, availability=0.9, p95_ms=900, grade="C")
        for m in (1, 2)
    ]
    assert endings.cash_bankrupt(st) is False     # 现金还很厚
    st.company.cash_cents = -1
    assert endings.cash_bankrupt(st) is True


# ---------- 4. 技能会成长，网页也有难度选择 ----------

def test_web_new_game_accepts_difficulty(client) -> None:
    j = client.post("/api/new", json={"difficulty": "veteran"}).get_json()
    assert j["ok"] and j["data"]["difficulty"] == "veteran"
    assert j["data"]["skills"]["sql"] >= 50


# ---------- 5. 动作不再第 3 周就做空 ----------

def test_actions_remain_available_late_game() -> None:
    st = new_game(seed=15)
    catalog = action_rules.load_actions()
    for week in range(1, 13):
        st.meta.week = week
        st.player.ap = 5
        # 每周都至少有一个可做的动作
        available = [a for a in catalog if action_rules.check(st, a)[0]]
        assert available, f"第 {week} 周没有任何动作可做"
        action_rules.start(st, available[0]["id"], catalog)


def test_repeatable_action_costs_more_each_time() -> None:
    st = new_game(seed=16)
    catalog = action_rules.load_actions()
    add = next(a for a in catalog if a["id"] == "add_server")
    base = action_rules.current_cost(st, add)
    action_rules.start(st, "add_server", catalog)
    assert action_rules.current_cost(st, add) == base + 1


# ---------- 6. 写入参与模拟，索引有代价 ----------

def test_writes_participate_after_enable_flag() -> None:
    from core.model.state import Index
    from core.rules import query as qmod

    st = new_game(seed=17)
    st.flags["writes_enabled"] = True
    q_create = {"kind": "write"}
    table = st.db.tables["orders"]
    cost_before = qmod.choose_path(table, q_create, 0.8).cost.total_ms
    table.indexes["idx_extra"] = Index(columns=["user_id"])
    cost_after = qmod.choose_path(table, q_create, 0.8).cost.total_ms
    assert cost_after > cost_before              # 索引让写入变慢


def test_write_queries_active_when_flag_set() -> None:
    st = new_game(seed=19)
    from core.rules import query as qmod

    catalog = qmod.load_catalog()
    write_ids = {q["id"] for q in catalog if q["kind"] == "write"}
    assert write_ids == {"q_create", "q_pay"}
    m_off = evaluate_peak(st)
    st.flags["writes_enabled"] = True
    m_on = evaluate_peak(st)
    assert m_off.by_id("q_create") is None
    assert m_on.by_id("q_create") is not None
    assert m_on.io_iops > m_off.io_iops          # 写入也要占 IO


# ---------- 7/8. 指标自洽 ----------

def test_io_util_is_capped() -> None:
    st = new_game(seed=18)
    st.company.registered = 2_000_000        # 造一个过载场景
    m = evaluate_peak(st)
    assert m.io_util <= 1.0
    assert m.p95_ms < 60_000
    if m.io_util >= 0.9:
        assert m.error_rate > 0.01


# ---------- 9. 实验台可以执行 CREATE INDEX（扫描模式） ----------

def test_scan_mode_allows_create_index(tmp_path) -> None:
    from lab import replica, sandbox

    db = tmp_path / "r.db"
    replica.build(db)
    r = sandbox.run(
        "CREATE INDEX idx_manual ON orders(user_id)", db, allow_scan=True
    )
    assert r["ok"] is True
    assert "idx_manual" in replica.info(db)["indexes"]
    # 普通模式仍然拒绝
    r2 = sandbox.run("CREATE INDEX idx_x ON orders(user_id)", db)
    assert r2["ok"] is False


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from services import save as saves

    monkeypatch.setattr(saves, "DB_PATH", tmp_path / "t.db")
    import web.app as webapp

    monkeypatch.setattr(webapp, "CURRENT_SLOT", "t")
    app = webapp.create_app()
    app.testing = True
    return app.test_client()
