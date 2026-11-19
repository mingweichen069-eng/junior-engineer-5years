"""v1.0 回归：知识不是奖励，是工具。

覆盖：没有资格门槛、索引有效性的物理判定、技术档案只记录不授权、
三维互不转换、重大事件的持续冲击与真实解决、咨询工具、生产回归用例、
精力牵制、述职改读三维、跨局玩家知识。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.engine import new_game, step  # noqa: E402
from core.rules import actions as ar  # noqa: E402
from core.rules import events as event_rules  # noqa: E402
from core.rules import exam as exam_engine  # noqa: E402
from core.rules import review as review_rules  # noqa: E402
from core.rules import tech, workbench  # noqa: E402


# ---------- 1. 资格链已拆除 ----------

def test_no_action_has_tech_gate():
    st = new_game(seed=1)
    acts = ar.load_actions()
    assert not [a for a in acts if (a.get("requires") or {}).get("tech")]
    a = next(x for x in acts if x["id"] == "rewrite_dashboard")
    ok, why = ar.check(st, a)
    assert ok, why          # 没「学过」也能直接改写


def test_archive_records_but_never_gates():
    st = new_game(seed=2)
    workbench.mark(st, "db_t1", "seen")
    rows = tech.archive_rows(st)
    assert any(r["id"] == "db_t1" and r["seen"] for r in rows)
    a = next(x for x in ar.load_actions() if x["id"] == "online_ddl")
    ok, why = ar.check(st, a)
    assert ok, why          # 档案里没「部署过」也不拦你


# ---------- 2. 物理判定：索引有没有用，不看玩家会不会 ----------

def test_index_effectiveness_is_physical():
    st = new_game(seed=3)
    good = workbench.deploy_index(st, "CREATE INDEX ix_user ON orders(user_id, created_at)")
    assert good["ok"] and "q_my_orders" in good["effective_for"]
    bad = workbench.deploy_index(st, "CREATE INDEX ix_amount ON orders(amount)")
    assert bad["ok"] and bad["effective_for"] == []     # 允许建，但物理上没用
    assert "没有帮到任何线上查询" in bad["message"]


def test_wrong_index_still_costs_write_and_disk():
    st = new_game(seed=4)
    before = tech.mods(st)
    workbench.deploy_index(st, "CREATE INDEX ix_amount ON orders(amount)")
    after = tech.mods(st)
    assert after["io_wait"] > before["io_wait"]         # 写放大真实上涨
    assert after["per_query"] == {}                     # 却没有任何查询收益
    assert "ix_amount" in st.db.tables["orders"].indexes  # 磁盘占用也真实存在


def test_non_sargable_predicate_needs_rewrite():
    st = new_game(seed=5)
    r = workbench.deploy_index(st, "CREATE INDEX ix_created ON orders(created_at)")
    assert "q_dashboard" not in r["effective_for"]      # DATE() 包住列，建了也白建
    assert workbench.deploy_rewrite(st, "q_dashboard", workbench.REFERENCE_REWRITES["q_dashboard"])["ok"]
    assert "q_dashboard" in st.assets.indexes[0].effective_for  # 改写后物理生效


def test_experiment_uses_real_sqlite(tmp_path, monkeypatch):
    monkeypatch.setattr(workbench, "LAB_DB", tmp_path / "lab.db")
    st = new_game(seed=6)
    r = workbench.experiment(st, "CREATE INDEX ix_lab ON orders(status)")
    assert r["ok"]
    r2 = workbench.inspect(st, "SELECT * FROM orders WHERE status = 1 LIMIT 5")
    assert r2["ok"] and r2["plan"]                     # 真执行计划，不是模拟出来的


# ---------- 3. 三维互不转换 ----------

def test_knowledge_does_not_change_simulation_but_assets_do():
    st = new_game(seed=7)
    base = tech.mods(st)["query_time"]
    workbench.read_card(st, "k_arch_hierarchy")
    workbench.mark(st, "db_t1", "seen")
    assert tech.mods(st)["query_time"] == base          # 懂了 ≠ 公司有了
    workbench.deploy_index(st, "CREATE INDEX ix_user ON orders(user_id, created_at)")
    assert tech.mods(st)["per_query"]["q_my_orders"] < 1.0


def test_all_knowledge_is_readable():
    rows = tech.knowledge_rows(new_game(seed=8))
    assert len(rows) == 44
    assert all(not r["locked"] and r["body"] for r in rows)  # 文档，不是奖励


def test_review_reads_assets_not_exams():
    d = review_rules.compute(new_game(seed=9))
    ids = [a["id"] for a in d["achievements"]]
    assert "assets" in ids and "incident" in ids
    assert "exam" not in ids and "tech" not in ids
    assert any("公司资产" in k["label"] for k in d["kpis"])


def test_energy_penalty_from_actions():
    st = new_game(seed=10)
    st.actions.actions_this_week = 6
    assert tech.energy_penalty(st) == 8
    assert tech.energy_regen(st) < 12


# ---------- 4. 重大事件：持续冲击 → 真的解决 ----------

def test_major_event_persists_until_really_resolved():
    st = new_game(seed=11)
    for _ in range(5):
        step(st)
    assert any(a["id"] == "M01_dashboard_degrade" for a in st.events.active_major)
    a = next(x for x in st.events.active_major if x["id"] == "M01_dashboard_degrade")
    assert a["weeks"] >= 1                              # 每周都在恶化
    assert workbench.deploy_rewrite(st, "q_dashboard", workbench.REFERENCE_REWRITES["q_dashboard"])["ok"]
    step(st)
    assert not any(x["id"] == "M01_dashboard_degrade" for x in st.events.active_major)
    assert any(r["id"] == "M01_dashboard_degrade" for r in st.career_rec.resolved_events)


def test_major_event_only_resolves_by_assets():
    st = new_game(seed=12)
    for _ in range(5):
        step(st)
    # 光「查阅知识 / 记进档案」不会让事件消失
    workbench.read_card(st, "k_arch_hierarchy")
    workbench.mark(st, "db_t2", "experimented")
    event_rules.tick_major(st)
    assert any(a["id"] == "M01_dashboard_degrade" for a in st.events.active_major)


def test_consult_costs_energy_and_gives_observation():
    st = new_game(seed=13)
    for _ in range(5):
        step(st)
    e0 = st.player.energy
    r = event_rules.consult(st, "M01_dashboard_degrade")
    assert r["ok"] and r["level"] == 1 and r["message"]
    assert st.player.energy < e0
    assert "该" not in r["message"][:20]                # 线索是观察，不是「你该学 X」


def test_fix_deploys_only_when_regression_cases_pass():
    st = new_game(seed=14)
    tasks = exam_engine.load()["tasks"]
    cid = "ex_fn_idempo"
    assert not workbench.deploy_fix(st, cid, "pass")["ok"]        # 跑不通就不部署
    assert cid not in st.assets.fixes
    assert workbench.deploy_fix(st, cid, tasks[cid]["solution"])["ok"]
    assert cid in st.assets.fixes


def test_fix_resolves_its_major_event():
    st = new_game(seed=15)
    tasks = exam_engine.load()["tasks"]
    for _ in range(13):
        step(st)
    assert any(a["id"] == "M02_duplicate_callback" for a in st.events.active_major)
    workbench.deploy_fix(st, "ex_fn_idempo", tasks["ex_fn_idempo"]["solution"])
    step(st)
    assert not any(a["id"] == "M02_duplicate_callback" for a in st.events.active_major)


# ---------- 5. 跨局：玩家知识永久，公司资产不继承 ----------

def test_profile_syncs_player_knowledge_only(tmp_path, monkeypatch):
    from services import save as saves

    monkeypatch.setattr(saves, "DB_PATH", tmp_path / "profile.db")
    st = new_game(seed=16)
    workbench.read_card(st, "k_arch_hierarchy")
    workbench.deploy_index(st, "CREATE INDEX ix_user ON orders(user_id, created_at)")
    prof = saves.sync_profile(st)
    assert "k_arch_hierarchy" in prof["player_knowledge"]
    assert "unlocked_knowledge" not in prof
    assert "db_t1" in prof["practiced_tech"]
