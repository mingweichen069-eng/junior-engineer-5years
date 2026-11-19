"""v1.1.2 阶段三回归：查询改写必须真执行玩家提交的 SQL。

对应任务书第五节与第九节：
  5  玩家 SQL 改写错误时不能错误地标记为成功
  6  改写 SQL 的查询结果必须与原查询保持一致
  7  无效索引 / 无效改写不能获得性能收益（这里测「没用上索引的改写」）
  9  生产模拟读取真实部署资产，而不是一个 rewritten=True 标志
 12  部署 / 验证 / 重复部署的成本与资产状态各自独立

原则：两份 SQL 都真的在 SQLite 里跑，不用硬编码「通过」；不删旧测试；
不重新引入任何科技树 / 考试解锁。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.engine import new_game  # noqa: E402
from core.rules import actions as ar  # noqa: E402
from core.rules import workbench  # noqa: E402

GOOD = workbench.REFERENCE_REWRITES["q_dashboard"]
ORIGINAL = next(q["sql"] for q in workbench.catalog() if q["id"] == "q_dashboard")
# 结果肯定与原查询不同：查了另一天，业务含义被改掉了
WRONG_RESULT = (
    "SELECT status, COUNT(*) AS cnt, SUM(amount) AS gmv FROM orders "
    "WHERE created_at >= '2023-11-03 00:00:00' AND created_at < '2023-11-04 00:00:00' "
    "GROUP BY status ORDER BY status"
)
# 语法就跑不起来
BROKEN = "SELECT * FROM orders WHERE"


@pytest.fixture(autouse=True)
def isolated_lab(tmp_path, monkeypatch):
    """每个测试用独立实验库；关掉事故掷骰，让断言只测改写本身。"""
    monkeypatch.setattr(workbench, "LAB_DB", tmp_path / "lab.db")
    monkeypatch.setattr(workbench, "_roll_incident", lambda *a, **k: False)


def _rich(seed: int = 401):
    st = new_game(seed=seed)
    st.player.ap = 999
    st.player.energy = 999.0
    return st


# ---------- 1. 真执行：两份查询都跑过，结果一致且用上索引 ----------

def test_check_rewrite_runs_both_queries_and_compares_results():
    st = _rich()
    r = workbench.check_rewrite(st, "q_dashboard", GOOD)
    assert r["ok"], r["message"]
    assert r["rows_equal"] is True
    assert r["verified"] is True
    assert r["original"]["ok"] and r["submitted"]["ok"]
    assert r["original"]["plan"] and r["submitted"]["plan"]
    # 原始用 DATE() 包住列 -> 没用上索引；改写后真的 SEARCH ... USING INDEX
    assert r["original"]["used_index"] is False
    assert r["submitted"]["used_index"] is True
    assert any("created_at" in str(p.get("detail", "")).lower()
               for p in r["submitted"]["plan"])
    assert r["original"]["row_count"] == r["submitted"]["row_count"] > 0


def test_check_rewrite_does_not_mutate_state():
    st = _rich()
    ap0, energy0, n_exp = st.player.ap, st.player.energy, len(st.lab.experiments)
    workbench.check_rewrite(st, "q_dashboard", GOOD)
    assert (st.player.ap, st.player.energy, len(st.lab.experiments)) == (ap0, energy0, n_exp)
    assert not st.assets.rewrite_sql


# ---------- 2/3. 错误 SQL 与结果不一致都不能标记成功 ----------

@pytest.mark.parametrize("sql, needle", [
    (BROKEN, "跑不起来"),
    ("UPDATE orders SET amount = 1", "只允许 SELECT"),
    ("", "还没有提交"),
    (WRONG_RESULT, "结果不一致"),
])
def test_bad_rewrite_is_never_marked_successful(sql, needle):
    st = _rich()
    r = workbench.deploy_rewrite(st, "q_dashboard", sql)
    assert r["ok"] is False, (sql, r)
    assert needle in r["message"], r["message"]
    assert r["charged"] is True
    # 失败发布只收勘察 / 回滚成本，不登记任何资产
    assert r["cost"]["ap"] == ar.deploy_cost("rewrite")["survey_ap"]
    assert "q_dashboard" not in st.assets.rewrite_sql
    assert "q_dashboard" not in st.assets.rewritten_queries
    assert not st.flags.get("q_dashboard_rewritten")
    assert r["rewrite_check"]["rows_equal"] is False or not r["rewrite_check"]["submitted"]["ok"]


def test_result_mismatch_reports_both_plans():
    st = _rich()
    r = workbench.deploy_rewrite(st, "q_dashboard", WRONG_RESULT)
    check = r["rewrite_check"]
    assert check["rows_equal"] is False
    assert check["original"]["row_count"] > 0      # 两份都真的跑出了行
    assert check["submitted"]["row_count"] > 0
    assert check["original"]["plan"] and check["submitted"]["plan"]


# ---------- 4. 有效改写：SQL 进资产，生产模拟真的读资产 ----------

def test_valid_rewrite_stores_sql_and_changes_production_simulation():
    st = _rich()
    # v1.1.3：初始 schema 已有 created_at 索引，不需要玩家重复部署。
    assert not st.assets.indexes
    assert "q_dashboard" not in workbench.effective_for(st, "orders", ["created_at"])

    r = workbench.deploy_rewrite(st, "q_dashboard", GOOD)
    assert r["ok"] and r["verified"] is True
    assert st.assets.rewrite_sql["q_dashboard"] == GOOD.strip()
    assert st.assets.rewrite_verified["q_dashboard"] is True
    assert "q_dashboard" in st.assets.rewritten_queries
    assert st.flags.get("q_dashboard_rewritten") is True
    # 生产初始索引在改写后按新谓词生效，per_query 收益来自真实生产索引
    assert "q_dashboard" in workbench.effective_for(st, "orders", ["created_at"])
    assert workbench.mods(st)["per_query"]["q_dashboard"] < 1.0
    assert not st.assets.indexes          # 没有重复部署，也没有伪造资产
    assert workbench.is_rewritten(st, "q_dashboard") is True


def test_rewrite_without_index_gain_has_no_production_benefit():
    """结果一致但没用上目标索引：允许部署（不设门槛），但资产标记为无效。"""
    st = _rich()
    r = workbench.deploy_rewrite(st, "q_dashboard", ORIGINAL)
    assert r["ok"] is True                 # 不是「错误」，只是一次没有收益的发布
    assert r["verified"] is False
    assert st.assets.rewrite_sql["q_dashboard"] == ORIGINAL.strip()
    assert st.assets.rewrite_verified["q_dashboard"] is False
    assert "q_dashboard" not in st.assets.rewritten_queries   # 不能靠标志骗过事件判定
    assert not st.flags.get("q_dashboard_rewritten")
    assert "q_dashboard" not in workbench.effective_for(st, "orders", ["created_at"])
    assert "q_dashboard" not in workbench.mods(st)["per_query"]


# ---------- 5. 去重 / 成本 / 实验记录 ----------

def test_duplicate_valid_rewrite_is_not_charged_twice():
    st = _rich()
    first = workbench.deploy_rewrite(st, "q_dashboard", GOOD)
    assert first["ok"]
    ap0 = st.player.ap
    n = len(st.assets.rewritten_queries)
    second = workbench.deploy_rewrite(st, "q_dashboard", GOOD)
    assert second["ok"] is False and second.get("duplicate") is True
    assert second["charged"] is False
    assert st.player.ap == ap0
    assert len(st.assets.rewritten_queries) == n


def test_check_rewrite_for_client_records_experiment_and_only_costs_energy():
    st = _rich()
    ap0, energy0 = st.player.ap, st.player.energy
    r = workbench.check_rewrite_for_client(st, "q_dashboard", GOOD)
    assert r["ok"] and r["verified"] is True
    assert st.player.ap == ap0
    assert st.player.energy < energy0
    assert workbench.has_experiment(st, "rewrite", workbench._signature("rewrite", GOOD))
    # 实验只是降低随后部署的风险，绝不自动变成公司资产
    assert not st.assets.rewrite_sql and not st.assets.rewritten_queries
    assert workbench.risk_of(st, "rewrite", workbench._signature("rewrite", GOOD))["verified"] is True


# ---------- 6. API：必须带 SQL，且验证端点不部署 ----------

def test_api_rewrite_without_sql_is_rejected(client):
    client.post("/api/new", json={"seed": 402, "difficulty": "normal"})
    r = client.post("/api/workbench/deploy-rewrite",
                    json={"query": "q_dashboard"}).get_json()
    assert r["ok"] is False
    assert "改写没通过生产校验" in r["message"] or "还没有提交" in r["message"]
    st = client.get("/api/state").get_json()["data"]
    assert "q_dashboard" not in st["assets"]["rewritten_queries"]


def test_api_check_rewrite_validates_but_does_not_deploy(client):
    client.post("/api/new", json={"seed": 403, "difficulty": "normal"})
    ap0 = client.get("/api/state").get_json()["data"]["ap"]
    r = client.post("/api/workbench/check-rewrite",
                    json={"query": "q_dashboard", "sql": GOOD}).get_json()
    assert r["ok"] is True and r["detail"]["verified"] is True
    assert r["detail"]["rows_equal"] is True
    st = client.get("/api/state").get_json()["data"]
    assert st["ap"] == ap0                      # 验证不花 AP
    assert "q_dashboard" not in st["assets"]["rewritten_queries"]
    assert "original_sql" in st["rewrite_queries"][0]
    assert "solution" not in str(st["rewrite_queries"]).lower()   # 参考答案不进 snapshot


def test_api_deploy_rewrite_stores_submitted_sql(client, monkeypatch):
    monkeypatch.setattr(workbench, "_roll_incident", lambda *a, **k: False)
    client.post("/api/new", json={"seed": 404, "difficulty": "normal"})
    r = client.post("/api/workbench/deploy-rewrite",
                    json={"query": "q_dashboard", "sql": GOOD}).get_json()
    assert r["ok"] is True, r["message"]
    st = client.get("/api/state").get_json()["data"]
    assert st["assets"]["rewrite_sql"]["q_dashboard"] == GOOD.strip()
    assert st["assets"]["rewrite_verified"]["q_dashboard"] is True
    assert r["detail"]["rewrite_check"]["rows_equal"] is True


def test_api_rejects_result_mismatch_and_persists_survey_cost(client):
    client.post("/api/new", json={"seed": 405, "difficulty": "normal"})
    ap0 = client.get("/api/state").get_json()["data"]["ap"]
    r = client.post("/api/workbench/deploy-rewrite",
                    json={"query": "q_dashboard", "sql": WRONG_RESULT}).get_json()
    assert r["ok"] is False and "结果不一致" in r["message"]
    after = client.get("/api/state").get_json()["data"]["ap"]
    assert after < ap0
    assert client.get("/api/state").get_json()["data"]["ap"] == after  # 刷新不回滚成本


# ---------- 7. 兼容与静态回归 ----------

def test_old_save_falls_back_to_rewritten_queries():
    """旧存档 / 行动面板老路径只有 rewritten_queries，效果不能倒退。"""
    st = _rich()
    st.assets.rewritten_queries.append("q_dashboard")
    assert workbench.is_rewritten(st, "q_dashboard") is True
    st.assets.rewrite_verified["q_dashboard"] = False   # 新资产显式判定优先
    assert workbench.is_rewritten(st, "q_dashboard") is False


def test_frontend_has_no_preset_answer_button():
    js = (ROOT / "web" / "static" / "app.js").read_text(encoding="utf-8")
    assert "把 DATE() 改成区间条件" not in js
    assert "data-wb-deploy-rewrite" in js
    assert "/api/workbench/check-rewrite" in js
    # 参考答案只存在于后端常量，不能出现在前端资源里
    assert "2023-11-03 00:00:00" not in js
