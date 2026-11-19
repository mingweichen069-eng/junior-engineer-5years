"""v0.4 审查修复与科技树的回归测试。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.engine import new_game, step  # noqa: E402
from core.rules import actions as ar  # noqa: E402
from core.rules import debt, endings, tech  # noqa: E402
from core.rules.evaluate import evaluate_peak  # noqa: E402


# ---------- 1. 技术债有再生能力 ----------

def test_debt_regenerates_and_upgrades() -> None:
    st = new_game(seed=1)
    base = len(st.debt.items)
    assert debt.spawn(st, "a", "query", 2.0, name="新债 A")
    assert debt.spawn(st, "b", "ops", 2.0, name="新债 B")
    assert len(st.debt.items) == base + 2
    # 同一条债重复产生 → 升级而不是重复添加
    before = st.debt.items["a"].severity
    assert debt.spawn(st, "a", "query", 4.0)
    assert len(st.debt.items) == base + 2
    assert st.debt.items["a"].severity >= before


def test_actions_and_events_spawn_debt() -> None:
    st = new_game(seed=2)
    assert "cache_invalidation" not in st.debt.items
    ar.start(st, "cache_dashboard")
    assert "cache_invalidation" in st.debt.items
    assert "server_sprawl" not in st.debt.items
    st.player.ap = 5
    ar.start(st, "add_server")
    assert "server_sprawl" in st.debt.items


# ---------- 2. 信息是资源：监控门槛 ----------

def test_queries_hidden_without_monitoring(client) -> None:
    client.post("/api/new", json={"seed": 3})
    d = client.get("/api/state").get_json()["data"]
    assert all(q["hidden"] for q in d["queries"])
    client.post("/api/action", json={"id": "open_monitoring"})
    client.post("/api/advance", json={"weeks": 2})     # 下周生效
    d = client.get("/api/state").get_json()["data"]
    assert all(not q["hidden"] and q["ms"] is not None for q in d["queries"])


# ---------- 3. A 轮门槛与文档收益递减 ----------

def test_funding_requires_real_growth() -> None:
    from core.model.state import MonthSnapshot

    def try_fund(registered: int, seed: int) -> bool:
        st = new_game(seed=seed)
        st.meta.week = 24
        st.company.registered = registered
        st.company.cash_cents = 100_000_00
        for item in st.debt.items.values():
            item.resolved = True
        st.months = [
            MonthSnapshot(month=m, revenue_cents=0, cost_cents=0, profit_cents=0,
                          cash_cents=st.company.cash_cents, availability=0.995, p95_ms=120, grade="A")
            for m in (4, 5, 6)
        ]
        st.history = [
            __import__("core.model.state", fromlist=["WeekSnapshot"]).WeekSnapshot(
                week=20 + i, peak_qps=78, p95_ms=120, error_rate=0.004,
                disk_used_gb=1.6, io_util=0.7, cash_cents=st.company.cash_cents)
            for i in range(4)
        ]
        endings.tick(st)
        return any("A 轮资格" in m for m in st.review.milestones)

    # 18 万 → 不够；20.5 万 → 有机会（取决于融资窗口种子）
    assert not try_fund(180_000, 1)
    funded = any(try_fund(205_000, seed) for seed in range(1, 20))
    assert funded


def test_docs_trust_is_diminishing() -> None:
    st = new_game(seed=4)
    gains = []
    for _ in range(3):
        before = st.org.manager_trust
        st.player.ap = 5
        ok, _ = ar.start(st, "write_docs")
        assert ok
        st.meta.week += 1
        ar.apply_due(st)                    # 手动到期，避开事件噪音
        gains.append(round(st.org.manager_trust - before, 2))
    assert gains[0] >= gains[1] >= gains[2]
    assert gains[0] == 2 and gains[1] == 1 and gains[2] == 0


# ---------- 4. 科技树：读条 + 考核 ----------

# ---------- v0.8：时间读条 + 自学精力 ----------

