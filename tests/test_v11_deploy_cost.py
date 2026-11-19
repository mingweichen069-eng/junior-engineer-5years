"""v1.1 阶段一回归：生产变更必须有真实成本，且 API 绕不过去。

对应任务书第九节：
  1  玩家没有实验就直接部署索引
  2  未经实验的部署产生更高风险
  3  实验后部署可以降低风险
  4  重复部署相同索引不会重复扣费或重复登记
  7  失败的部署也应产生合理成本（本次额外覆盖）
  8  防止通过重复调用 API 绕过资源扣除
 10  API 直接调用不能绕过行动点和精力校验
 12 重置实验库不能删除公司已经部署的生产资产
  7  无效索引不能获得性能收益（旧用例仍由 test_v10_workbench 覆盖）
  8  索引写入成本必须正确计入（旧用例仍由 test_v10_workbench 覆盖）

原则：不删除旧测试、不硬编码「通过」、不重新引入科技树 / 考试解锁。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.engine import new_game  # noqa: E402
from core.model.serde import from_jsonable, to_jsonable  # noqa: E402
from core.model.state import GameState  # noqa: E402
from core.rules import actions as ar  # noqa: E402
from core.rules import tech, workbench  # noqa: E402

GOOD_INDEX = "CREATE INDEX ix_probe ON orders(user_id, created_at)"
BAD_INDEX = "CREATE INDEX ix_bad ON orders(amount)"


# ---------- 1 / 2：不实验直接上线，成本与风险都真实 ----------

def test_deploy_without_experiment_costs_ap_energy_and_time(monkeypatch):
    # 固定成「没出事故」的路径，成本断言不依赖随机掷骰
    import core.rules.workbench as wb
    monkeypatch.setattr(wb, "_roll_incident", lambda *a, **k: False)
    st = new_game(seed=101)
    ap0, energy0, actions0 = st.player.ap, st.player.energy, st.actions.actions_this_week
    r = workbench.deploy_index(st, GOOD_INDEX)

    assert r["ok"], r.get("message")
    assert r["charged"] is True
    assert st.player.ap == ap0 - ar.deploy_cost("index")["ap"]
    assert st.player.energy == energy0 - ar.deploy_cost("index")["energy"]
    # 时间成本：占本周一个动作位（会牵制下周精力回复）+ 一个发布窗口
    assert st.actions.actions_this_week == actions0 + 1
    assert st.flags.get("change_week") == st.meta.week + 1
    assert r["change_window_ms"] > 0
    # 上线后才登记为公司资产，实验记录里什么都没有
    assert st.assets.indexes and not st.lab.experiments
    assert len(st.lab.deployments) == 1
    assert st.lab.deployments[0]["verified"] is False


def test_unverified_deploy_has_higher_risk_than_verified(tmp_path, monkeypatch):
    # 实验库是全局文件，测试之间要隔离，避免同名索引「already exists」
    monkeypatch.setattr(workbench, "LAB_DB", tmp_path / "lab.db")
    st = new_game(seed=102)
    unverified = workbench.risk_of(st, "index", workbench._signature("index", GOOD_INDEX,
                                                                     workbench.parse_index(GOOD_INDEX)))
    assert unverified["verified"] is False
    assert unverified["untested"] > 0

    # 真的在实验库里验证过同一个方案（同表同列），而不是硬编码一个标志
    st2 = new_game(seed=102)
    exp = workbench.experiment(st2, "CREATE INDEX ix_lab ON orders(user_id, created_at)")
    assert exp["ok"] and exp["experiment_recorded"] is True
    key = workbench._signature("index", GOOD_INDEX, workbench.parse_index(GOOD_INDEX))
    verified = workbench.risk_of(st2, "index", key)

    assert verified["verified"] is True
    assert verified["untested"] == 0
    assert verified["total"] < unverified["total"]
    assert workbench.has_experiment(st2, "index", key)
    # 实验只是个人经验，不会凭空变成公司资产
    assert not st2.assets.indexes


def test_experiment_then_deploy_reduces_risk(tmp_path, monkeypatch):
    """3：实验后部署可以降低风险 —— 同一存档里前后对比，不是两个不同局。"""
    monkeypatch.setattr(workbench, "LAB_DB", tmp_path / "lab.db")
    st = new_game(seed=103)
    key = workbench._signature("index", GOOD_INDEX, workbench.parse_index(GOOD_INDEX))
    before = workbench.risk_of(st, "index", key)["total"]
    exp = workbench.experiment(st, "CREATE INDEX ix_lab ON orders(user_id, created_at)")
    assert exp["ok"] and exp["experiment_recorded"] is True
    after = workbench.risk_of(st, "index", key)["total"]
    assert after < before
    r = workbench.deploy_index(st, GOOD_INDEX)
    assert r["ok"] and r["risk"]["verified"] is True


def test_consecutive_changes_raise_risk():
    """本周连着上线没有观察期，风险递增（时间成本的一层）。"""
    st = new_game(seed=104)
    k1 = workbench._signature("index", GOOD_INDEX, workbench.parse_index(GOOD_INDEX))
    k2 = workbench._signature("index", BAD_INDEX, workbench.parse_index(BAD_INDEX))
    r1 = workbench.risk_of(st, "index", k1)
    workbench.deploy_index(st, GOOD_INDEX)
    r2 = workbench.risk_of(st, "index", k2)
    assert r2["changes_this_week"] == 1
    assert r2["consecutive"] > r1["consecutive"]
    assert r2["total"] > r1["total"]


# ---------- 4 / 8：去重与防绕过 ----------

def test_duplicate_deploy_does_not_charge_or_register_twice():
    st = new_game(seed=105)
    first = workbench.deploy_index(st, GOOD_INDEX)
    assert first["ok"]
    ap_after_first = st.player.ap
    assets_after_first = len(st.assets.indexes)
    deploys_after_first = len(st.lab.deployments)

    second = workbench.deploy_index(st, GOOD_INDEX)
    assert not second["ok"] and second.get("duplicate") is True
    assert second["charged"] is False
    assert st.player.ap == ap_after_first                   # 不重复扣费
    assert len(st.assets.indexes) == assets_after_first     # 不重复登记
    assert len(st.lab.deployments) == deploys_after_first  # 不重复记流水

    # 换个名字但同表同列，也算重复（防止靠改名刷资产 / 刷收益）
    third = workbench.deploy_index(st, "CREATE INDEX ix_other ON orders(user_id, created_at)")
    assert not third["ok"] and third.get("duplicate") is True
    assert st.player.ap == ap_after_first


def test_failed_deploy_still_costs_survey_and_rollback():
    """7：写错的 SQL / 跑挂的回归也是一次发布尝试，工程师确实花了时间。"""
    st = new_game(seed=106)
    ap0, energy0 = st.player.ap, st.player.energy
    r = workbench.deploy_index(st, "这不是一条 CREATE INDEX")
    assert not r["ok"] and r["charged"] is True
    assert st.player.ap == ap0 - ar.deploy_cost("index")["survey_ap"]
    assert st.player.energy == energy0 - ar.deploy_cost("index")["survey_energy"]
    assert not st.assets.indexes                            # 失败不登记资产
    assert st.lab.deployments[-1]["ok"] is False

    st2 = new_game(seed=107)
    ap1 = st2.player.ap
    bad_fix = workbench.deploy_fix(st2, "ex_fn_idempo", "pass")
    assert not bad_fix["ok"] and bad_fix["charged"] is True
    assert st2.player.ap == ap1 - ar.deploy_cost("fix")["survey_ap"]
    assert "ex_fn_idempo" not in st2.assets.fixes


def test_deploy_fix_no_longer_only_costs_energy():
    """deploy_fix 必须消耗行动点（原来只扣 4 精力）。"""
    st = new_game(seed=108)
    from core.rules import exam as exam_engine
    code = exam_engine.load()["tasks"]["ex_fn_idempo"]["solution"]
    ap0 = st.player.ap
    r = workbench.deploy_fix(st, "ex_fn_idempo", code)
    assert r["ok"] and st.player.ap == ap0 - ar.deploy_cost("fix")["ap"]


# ---------- 8 / 10：API 层统一校验 ----------

def test_api_cannot_bypass_ap_check(client):
    """直接 POST 部署接口也绕不过行动力校验。"""
    client.post("/api/new", json={"seed": 201, "difficulty": "normal"})
    # 把行动力压到不足以部署
    from services import save as saves
    import web.app as webapp
    st = saves.load_game(webapp.CURRENT_SLOT)
    st.player.ap = 1
    saves.save_game(webapp.CURRENT_SLOT, st, "测试")
    r = client.post("/api/workbench/deploy-index", json={"sql": GOOD_INDEX}).get_json()
    assert r["ok"] is False and "行动力不足" in r["message"]
    assert r["detail"]["charged"] is False
    assert not saves.load_game(webapp.CURRENT_SLOT).assets.indexes


def test_api_cannot_bypass_energy_check(client):
    client.post("/api/new", json={"seed": 202, "difficulty": "normal"})
    from services import save as saves
    import web.app as webapp
    st = saves.load_game(webapp.CURRENT_SLOT)
    st.player.energy = 0.0
    saves.save_game(webapp.CURRENT_SLOT, st, "测试")
    r = client.post("/api/workbench/deploy-index", json={"sql": GOOD_INDEX}).get_json()
    assert r["ok"] is False and "调查精力不足" in r["message"]
    assert not saves.load_game(webapp.CURRENT_SLOT).assets.indexes


def test_api_failed_deploy_cost_is_persisted(client):
    """关键漏洞：失败部署扣的成本必须存档，刷新后不能凭空消失。

    原实现只在 ok 时 save_game，失败一刷新资源就回滚 —— 等于可以无限重试。
    """
    client.post("/api/new", json={"seed": 203, "difficulty": "normal"})
    before = client.get("/api/state").get_json()["data"]["ap"]
    r = client.post("/api/workbench/deploy-index", json={"sql": " nonsense "}).get_json()
    assert r["ok"] is False and r["detail"]["charged"] is True
    after = client.get("/api/state").get_json()["data"]["ap"]
    assert after < before
    # 再取一次（模拟刷新 / 重新载入存档）成本依然在
    assert client.get("/api/state").get_json()["data"]["ap"] == after
    # 连点也不能把行动力刷回来
    for _ in range(3):
        client.post("/api/workbench/deploy-index", json={"sql": " nonsense "})
    assert client.get("/api/state").get_json()["data"]["ap"] < before


def test_api_all_three_deploys_charge(client):
    """索引 / 改写 / 修复三个端点都扣行动点，且走同一个校验入口。"""
    client.post("/api/new", json={"seed": 204, "difficulty": "normal"})
    from services import save as saves
    import web.app as webapp
    from core.rules import exam as exam_engine

    ap0 = client.get("/api/state").get_json()["data"]["ap"]
    r1 = client.post("/api/workbench/deploy-index", json={"sql": GOOD_INDEX}).get_json()
    assert r1["ok"] and r1["cost"]["ap"] == ar.deploy_cost("index")["ap"]

    r2 = client.post("/api/workbench/deploy-rewrite", json={
        "query": "q_dashboard", "sql": workbench.REFERENCE_REWRITES["q_dashboard"]}).get_json()
    assert r2["ok"] and r2["cost"]["ap"] == ar.deploy_cost("rewrite")["ap"]

    code = exam_engine.load()["tasks"]["ex_fn_idempo"]["solution"]
    r3 = client.post("/api/workbench/deploy-fix",
                     json={"case_id": "ex_fn_idempo", "code": code}).get_json()
    assert r3["ok"] and r3["cost"]["ap"] == ar.deploy_cost("fix")["ap"]

    assert client.get("/api/state").get_json()["data"]["ap"] < ap0
    st = saves.load_game(webapp.CURRENT_SLOT)
    assert len(st.lab.deployments) == 3
    assert {d["kind"] for d in st.lab.deployments} == {"index", "rewrite", "fix"}


# ---------- 12：重置实验库不动生产资产 ----------

def test_reset_lab_keeps_company_assets(tmp_path, monkeypatch):
    monkeypatch.setattr(workbench, "LAB_DB", tmp_path / "lab.db")
    st = new_game(seed=109)
    workbench.deploy_index(st, GOOD_INDEX)
    workbench.deploy_rewrite(st, "q_dashboard", workbench.REFERENCE_REWRITES["q_dashboard"])
    before_indexes = [tuple(i.columns) for i in st.assets.indexes]
    before_rewrites = list(st.assets.rewritten_queries)

    r = workbench.reset_lab()
    assert r["ok"]
    assert [tuple(i.columns) for i in st.assets.indexes] == before_indexes
    assert st.assets.rewritten_queries == before_rewrites
    assert st.flags.get("q_dashboard_rewritten") is True
    assert "ix_probe" in st.db.tables["orders"].indexes  # 生产表结构仍由资产驱动
    assert st.lab.deployments  # 流水也不被清掉
    # 只清实验库的物理表，公司资产 / 流水都不动
    assert len(st.lab.deployments) == 2
    assert workbench.changes_this_week(st) == 2


# ---------- 三态分离 / 序列化兼容 ----------

def test_lab_state_roundtrips_and_old_save_is_compatible():
    """实验 / 部署流水 / 公司资产是三种不同状态，且旧存档不需要迁移代码。"""
    st = new_game(seed=110)
    workbench.experiment(st, "CREATE INDEX ix_lab ON orders(user_id, created_at)")
    workbench.deploy_index(st, GOOD_INDEX)
    data = to_jsonable(st)
    restored = from_jsonable(GameState, data)
    assert restored.lab.experiments and restored.lab.deployments and restored.assets.indexes

    # 模拟 v1.0 旧存档：没有 lab 字段，serde 的 default_factory 自动补齐
    old = dict(data)
    old.pop("lab", None)
    migrated = from_jsonable(GameState, old)
    assert migrated.lab.experiments == [] and migrated.lab.deployments == []
    assert migrated.assets.indexes  # 原有资产不丢
    assert workbench.changes_this_week(migrated) == 0


def test_incident_is_engineering_consequence_not_exam_penalty():
    """强制掷出事故时，后果落在生产 / 组织 / 履历上，而不是扣分或冷却。"""
    import core.rules.workbench as wb
    st = new_game(seed=111)
    trusted = st.org.manager_trust
    tickets = st.org.tickets
    monkeypatch_roll = lambda *a, **k: True  # noqa: E731
    orig = wb._roll_incident
    wb._roll_incident = monkeypatch_roll
    try:
        r = workbench.deploy_index(st, GOOD_INDEX)
    finally:
        wb._roll_incident = orig
    assert r["ok"] and r["incident"] is True
    assert st.org.manager_trust < trusted and st.org.tickets > tickets
    assert st.career_rec.incidents and st.company.cash_cents < new_game(seed=111).company.cash_cents
    assert st.flags.get("lock_week") == st.meta.week + 1  # 下周 p95 / 错误率真实抖动
    assert st.assets.indexes                              # 部署仍成功，不是「不及格」
    assert not st.tech.entries.get("db_t1", {}).get("locked", False)


def test_wrong_index_still_costs_write_and_no_query_gain():
    """7 / 8（旧用例精神）：无效索引可以部署，但只有代价没有收益。"""
    st = new_game(seed=112)
    before = tech.mods(st)
    r = workbench.deploy_index(st, BAD_INDEX)
    assert r["ok"] and r["effective_for"] == []
    after = tech.mods(st)
    assert after["io_wait"] > before["io_wait"]
    assert after["per_query"] == {}
    assert "ix_bad" in st.db.tables["orders"].indexes
    assert r["charged"] is True
