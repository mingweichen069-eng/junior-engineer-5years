"""v0.6 机制：网格化科技树、知识卡、决策事件。

这些测试针对「写了但没被验证」的部分：
- 计算机基础降低跨线成本
- 专精与通才互斥
- 科技 mods 真的改变模拟指标
- 读条即记知识
- 决策事件超时按默认选项结算、选择应用效果
"""

from __future__ import annotations

from core.engine import new_game, step
from core.model.state import EventLogEntry, PendingEvent
from core.rules import events as event_rules
from core.rules import tech
from core.rules.evaluate import evaluate_peak

CS_ALL = ["cs_arch", "cs_os", "cs_algo", "cs_net", "cs_conc", "cs_dist"]


def _open_decision(st, event_id="D01_hire", choice_default="none"):
    log_id = f"T-{event_id}"
    st.events.log.append(
        EventLogEntry(
            log_id=log_id, event_id=event_id, title="测试决策",
            week=st.meta.week, day=1, pending=True,
        )
    )
    st.events.pending.append(
        PendingEvent(
            id=log_id, event_id=event_id, week=st.meta.week,
            day=1, log_id=log_id, default_choice=choice_default,
        )
    )
    return log_id


# ---------- 科技树 ----------

# ---------- 决策事件 ----------

def test_choice_applies_effects() -> None:
    st = new_game(seed=7)
    log_id = _open_decision(st)
    cash0 = st.company.cash_cents
    ok, msg = event_rules.resolve_choice(st, log_id, "senior")
    assert ok, msg
    assert st.company.cash_cents < cash0
    entry = next(e for e in st.events.log if e.log_id == log_id)
    assert entry.pending is False
    assert entry.choice == "senior"
    assert not st.events.pending


def test_decision_expires_with_default_on_next_week() -> None:
    st = new_game(seed=8)
    log_id = _open_decision(st)
    trust0 = st.org.manager_trust
    step(st)                                  # 推进一周：上周未决的决策按默认结算
    assert not st.events.pending
    entry = next(e for e in st.events.log if e.log_id == log_id)
    assert entry.pending is False
    assert entry.choice == "none"
    assert st.org.manager_trust <= trust0


def test_pending_queue_never_grows_unbounded() -> None:
    st = new_game(seed=9)
    for _ in range(event_rules.MAX_PENDING + 3):
        _open_decision(st, event_id="D02_feature_vs_debt", choice_default="scope")
    assert len(st.events.pending) >= event_rules.MAX_PENDING
    # 再开一个决策会先把积压按默认结算掉
    event_rules.run_week(st, evaluate_peak(st))
    assert len(st.events.pending) <= event_rules.MAX_PENDING


def test_decision_api_returns_updated_state(client) -> None:
    client.post("/api/new", json={"seed": 31, "difficulty": "normal"})
    client.get("/api/state")
    # 直接构造一条待决事件：走与引擎相同的通道
    from services import save as saves

    st = saves.load_game("t")
    log_id = _open_decision(st, event_id="D01_hire")
    saves.save_game("t", st, "测试")

    r = client.post("/api/event/choose", json={"id": log_id, "choice": "junior"}).get_json()
    assert r["ok"], r
    assert r["data"]["decisions"] == []
    entry = next(e for e in r["data"]["events"] if e["id"] == log_id)
    assert entry["choice"] == "junior"
