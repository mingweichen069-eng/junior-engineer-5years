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

def test_tech_tree_requires_study_then_assessment(study_finish) -> None:
    """读条 → 考核机会 → 真作业。没有「技能够了就直接过」这条路了。"""
    from core.rules import exam as exam_rules

    st = new_game(seed=5)
    ok, msg, _detail = tech.submit_exam(st, "db_t1", "SELECT 1")
    assert not ok and "读条" in msg

    ok, msg = study_finish(st, "db_t1")
    assert ok and "考核" in msg
    node = tech.node_def(tech.load(), "db_t1")
    status = tech.node_status(st, node)
    assert status["exam_state"] == "open" and status["ready"]

    # 写错 → 未通过，冷却一周
    ok, msg, detail = tech.submit_exam(st, "db_t1", "SELECT * FROM users")
    assert not ok and "冷却" in msg
    assert st.tech.exams["db_t1"]["state"] == "cool"
    assert st.tech.progress["db_t1"] >= node["ap"]      # 第一次失败不清读条

    # 冷却结束后用参考解交卷
    st.meta.week += 1
    ok, msg, _ = tech.submit_exam(st, "db_t1", exam_rules.solution(node, st))
    assert ok, msg
    assert st.tech.unlocked["db_t1"]
    assert st.tech.direction == "db"


def test_second_failure_resets_progress(study_finish) -> None:
    st = new_game(seed=15)
    study_finish(st, "db_t1")
    tech.submit_exam(st, "db_t1", "SELECT 1")
    st.meta.week += 1
    ok, msg, _ = tech.submit_exam(st, "db_t1", "SELECT 1")
    assert not ok and "清零" in msg
    assert st.tech.progress["db_t1"] == 0
    assert "db_t1" not in st.tech.exams


def test_easy_mode_skips_exam(study_finish) -> None:
    st = new_game(seed=16, difficulty="easy")
    ok, msg = study_finish(st, "db_t1")
    assert ok and "已掌握" in msg
    assert st.tech.unlocked["db_t1"]


def test_timeout_counts_as_failure(study_finish) -> None:
    from core.engine import step as advance_week

    st = new_game(seed=17)
    study_finish(st, "db_t1")
    assert st.tech.exams["db_t1"]["state"] == "open"
    advance_week(st)
    assert st.tech.exams["db_t1"]["state"] == "cool"


def test_switching_direction_costs_double() -> None:
    st = new_game(seed=6)
    st.tech.unlocked["db_t1"] = True
    data = tech.load()
    be_t1 = tech.node_def(data, "be_t1")
    assert tech.study_cost(st, be_t1) == 2 * be_t1["ap"]   # 转向成本翻倍
    db_t2 = tech.node_def(data, "db_t2")
    st.tech.unlocked["db_t2"] = True
    assert tech.study_cost(st, db_t2) == db_t2["ap"]       # 同方向不翻倍


def test_exam_api_flow(client) -> None:
    """API 级：读条 → 考核 → 判题细节回传。"""
    client.post("/api/new", json={"seed": 7, "difficulty": "normal"})
    j = client.post("/api/tech/study", json={"node": "db_t1"}).get_json()
    assert j["ok"] and "开始读" in j["message"]
    for _ in range(12):
        d0 = client.get("/api/state").get_json()["data"]
        if any(e["node"] == "db_t1" for e in d0["tech"]["exams"]):
            break
        client.post("/api/advance", json={"weeks": 1})

    d = client.get("/api/state").get_json()["data"]
    assert any(e["node"] == "db_t1" and e["state"] == "open" for e in d["tech"]["exams"])

    task = client.get("/api/tech/exam?node=db_t1").get_json()["data"]["task"]
    assert task["kind"] == "sql" and task["starter"]

    bad = client.post("/api/tech/exam/submit", json={"node": "db_t1", "answer": "SELECT 1"}).get_json()
    assert bad["ok"] is False and bad["detail"]["plan"]

    from core.rules import exam as exam_rules
    from core.rules import tech as tech_rules

    node = tech_rules.node_def(tech_rules.load(), "db_t1")
    client.post("/api/advance", json={"weeks": 1})     # 等冷却
    good = client.post(
        "/api/tech/exam/submit",
        json={"node": "db_t1", "answer": exam_rules.solution(node)},
    ).get_json()
    assert good["ok"] is True
    node_state = next(n for n in good["data"]["tech"]["nodes"] if n["id"] == "db_t1")
    assert node_state["unlocked"] is True


def test_capstone_sets_ending_flavor(exam_pass) -> None:
    st = new_game(seed=8)
    st.tech.unlocked["db_t1"] = True
    st.tech.unlocked["db_t2"] = True
    ok, msg = exam_pass(st, "db_t3")
    assert ok, msg
    assert st.ending.flavor == "db"


# ---------- v0.8：时间读条 + 自学精力 ----------

def test_study_costs_no_ap() -> None:
    """科技完全不占行动力：读条只走时间，AP 一分不动。"""
    st = new_game(seed=101)
    ap0 = st.player.ap
    ok, msg = tech.toggle_study(st, "db_t1")
    assert ok and "开始读" in msg
    assert st.player.ap == ap0
    p0 = int(st.tech.progress.get("db_t1", 0))
    tech.tick_study(st)
    assert int(st.tech.progress.get("db_t1", 0)) > p0
    assert st.player.ap == ap0


def test_only_time_advances_the_bar() -> None:
    """点按钮只能开始/暂停，不能靠花资源加速。"""
    st = new_game(seed=102)
    ok, _ = tech.toggle_study(st, "db_t1")
    assert ok
    for _ in range(6):
        tech.toggle_study(st, "db_t1")
        tech.toggle_study(st, "db_t1")
    assert int(st.tech.progress.get("db_t1", 0)) == 0
    tech.tick_study(st)
    assert int(st.tech.progress.get("db_t1", 0)) > 0


def test_more_energy_reads_faster() -> None:
    """精力越高读得越快：100 精力那一周的推进量明显大于 0 精力。"""
    low = new_game(seed=103)
    high = new_game(seed=103)
    low.player.energy = 0
    high.player.energy = 100
    for st in (low, high):
        tech.toggle_study(st, "db_t1")
        tech.tick_study(st)
    assert tech.study_rate(high) >= tech.study_rate(low) + 4
    assert high.tech.progress["db_t1"] > low.tech.progress["db_t1"]


def test_energy_regen_and_consumption() -> None:
    """没在读就回精力，在读就消耗精力；上限不破。"""
    ctrl = new_game(seed=104)
    ctrl.player.energy = 0
    tech.tick_study(ctrl)
    assert ctrl.player.energy == 12                 # 普通难度每周 +12（纯回复）

    st = new_game(seed=104)
    st.player.energy = 0
    tech.toggle_study(st, "db_t1")
    tech.tick_study(st)
    assert st.player.energy < ctrl.player.energy    # 同样回复 12，但推进消耗了一部分

    st.player.energy = tech.ENERGY_CAP
    tech.tick_study(st)
    assert st.player.energy <= tech.ENERGY_CAP      # 上限不破


def test_switching_active_node_keeps_progress() -> None:
    """同时只能读一个：切换会暂停前一个，但进度保留。"""
    st = new_game(seed=105)
    tech.toggle_study(st, "db_t1")
    tech.tick_study(st)
    p1 = int(st.tech.progress.get("db_t1", 0))
    ok, msg = tech.toggle_study(st, "cs_arch")
    assert ok and st.tech.active == "cs_arch"
    assert int(st.tech.progress.get("db_t1", 0)) == p1     # 前一个节点进度保留
    tech.tick_study(st)
    assert int(st.tech.progress.get("db_t1", 0)) == p1     # 而且不再偷偷推进
    assert int(st.tech.progress.get("cs_arch", 0)) > 0
    ok, msg = tech.toggle_study(st, "db_t1")
    assert ok and st.tech.active == "db_t1"                # 切回来
    tech.toggle_study(st, "db_t1")
    assert st.tech.active == ""                            # 再点一次暂停


def test_study_api_start_and_pause(client) -> None:
    client.post("/api/new", json={"seed": 106, "difficulty": "normal"})
    j = client.post("/api/tech/study", json={"node": "db_t1"}).get_json()
    assert j["ok"] and "开始读" in j["message"]
    d = j["data"]
    assert d["studying"] == "db_t1" and d["energy"] >= 0
    j2 = client.post("/api/tech/study", json={"node": "db_t1"}).get_json()
    assert j2["ok"] and "暂停" in j2["message"] and j2["data"]["studying"] == ""
    d3 = client.post("/api/advance", json={"weeks": 1}).get_json()["data"]
    assert d3["energy"] > d["energy"]            # 暂停后这周精力纯回复
