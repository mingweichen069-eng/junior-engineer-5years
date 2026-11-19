"""v1.1.3 回归：SQL 改写的验证结果必须与部署的 SQL 完全一致。

覆盖：
- 验证 A 后改 B：旧验证失效，必须重新验证；
- 部署保存的 SQL == 实际验证通过的 SQL；
- 部署后改成无收益 SQL：旧的有效性与生产收益一起撤销；
- 结果不一致 / 没索引：不产生资产收益；
- 旧存档加载后验证记录缺失也能正常部署。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.engine import new_game  # noqa: E402
from core.model.serde import from_jsonable, to_jsonable  # noqa: E402
from core.model.state import GameState  # noqa: E402
from core.rules import workbench  # noqa: E402

A = workbench.REFERENCE_REWRITES["q_dashboard"]
B = A.replace("SELECT status", "SELECT  status")          # 语义相同、文本不同、仍走索引
ORIGINAL = next(q["sql"] for q in workbench.catalog() if q["id"] == "q_dashboard")
WRONG_RESULT = (
    "SELECT status, COUNT(*) AS cnt, SUM(amount) AS gmv FROM orders "
    "WHERE created_at >= '2023-11-03 00:00:00' AND created_at < '2023-11-04 00:00:00' "
    "GROUP BY status ORDER BY status"
)


@pytest.fixture(autouse=True)
def isolated_lab(tmp_path, monkeypatch):
    monkeypatch.setattr(workbench, "LAB_DB", tmp_path / "lab.db")
    monkeypatch.setattr(workbench, "_roll_incident", lambda *a, **k: False)


def _rich(seed: int = 701):
    st = new_game(seed=seed)
    st.player.ap = 999
    st.player.energy = 999.0
    return st


# ---------- 1. 验证 A 后部署 B：必须先重新验证 ----------

def test_modified_sql_after_validation_is_rejected_until_revalidated():
    st = _rich(seed=702)
    first = workbench.check_rewrite_for_client(st, "q_dashboard", A)
    assert first["ok"] and first["verified"] is True
    assert st.lab.rewrite_checks["q_dashboard"]["sql"] == A.strip()

    stale = workbench.deploy_rewrite(st, "q_dashboard", B)
    assert stale["ok"] is False
    assert stale.get("stale_validation") is True
    assert stale["charged"] is False
    assert "重新" in stale["message"]
    # 没有产生任何资产 / 流水 / 生产收益
    assert st.assets.rewrite_sql == {}
    assert st.assets.rewritten_queries == []
    assert st.lab.deployments == []
    assert "q_dashboard" not in workbench.mods(st)["per_query"]

    # 重新验证 B 之后可以部署 B
    assert workbench.check_rewrite_for_client(st, "q_dashboard", B)["verified"] is True
    deployed = workbench.deploy_rewrite(st, "q_dashboard", B)
    assert deployed["ok"] and deployed["verified"] is True
    assert st.assets.rewrite_sql["q_dashboard"] == B.strip()


# ---------- 2. 部署保存的是实际验证的 SQL ----------

def test_deploy_stores_exactly_the_validated_sql():
    st = _rich(seed=703)
    assert workbench.check_rewrite_for_client(st, "q_dashboard", A)["ok"]
    result = workbench.deploy_rewrite(st, "q_dashboard", A)
    assert result["ok"]
    assert st.assets.rewrite_sql["q_dashboard"] == A.strip()
    assert st.lab.rewrite_checks["q_dashboard"]["sql"] == A.strip()
    assert workbench.mods(st)["per_query"]["q_dashboard"] == pytest.approx(0.55)


# ---------- 3. 部署后修改 SQL：旧有效性必须撤销 ----------

def test_switching_to_no_gain_sql_revokes_old_effect():
    st = _rich(seed=704)
    assert workbench.deploy_rewrite(st, "q_dashboard", A)["ok"]
    assert workbench.mods(st)["per_query"]["q_dashboard"] == pytest.approx(0.55)
    assert st.flags.get("q_dashboard_rewritten") is True

    # 玩家改成原始（无索引收益）写法并验证通过
    check = workbench.check_rewrite_for_client(st, "q_dashboard", ORIGINAL)
    assert check["ok"] is True and check["verified"] is False
    result = workbench.deploy_rewrite(st, "q_dashboard", ORIGINAL)
    assert result["ok"] is True and result["verified"] is False

    assert st.assets.rewrite_sql["q_dashboard"] == ORIGINAL.strip()
    assert st.assets.rewrite_verified["q_dashboard"] is False
    assert "q_dashboard" not in st.assets.rewritten_queries
    assert st.flags.get("q_dashboard_rewritten") is False
    # 旧的索引收益不能继续生效
    assert "q_dashboard" not in workbench.mods(st)["per_query"]
    assert "q_dashboard" not in workbench.effective_for(st, "orders", ["created_at"])


# ---------- 4. 结果不一致 / 无收益：都不登记有效资产 ----------

def test_result_mismatch_never_registers_assets_or_benefit():
    st = _rich(seed=705)
    assert workbench.check_rewrite_for_client(st, "q_dashboard", WRONG_RESULT)["ok"] is False
    result = workbench.deploy_rewrite(st, "q_dashboard", WRONG_RESULT)
    assert result["ok"] is False
    assert st.assets.rewrite_sql == {}
    assert st.assets.rewritten_queries == []
    assert "q_dashboard" not in workbench.mods(st)["per_query"]


# ---------- 5. API 端到端：同一套状态校验 ----------

def test_api_modified_sql_requires_revalidation(client, monkeypatch):
    monkeypatch.setattr(workbench, "_roll_incident", lambda *a, **k: False)
    client.post("/api/new", json={"seed": 706, "difficulty": "normal"})

    checked = client.post("/api/workbench/check-rewrite",
                          json={"query": "q_dashboard", "sql": A}).get_json()
    assert checked["ok"] is True and checked["detail"]["verified"] is True

    stale = client.post("/api/workbench/deploy-rewrite",
                        json={"query": "q_dashboard", "sql": B}).get_json()
    assert stale["ok"] is False
    assert stale["detail"].get("stale_validation") is True
    st = client.get("/api/state").get_json()["data"]
    assert "q_dashboard" not in st["assets"]["rewrite_sql"]

    client.post("/api/workbench/check-rewrite", json={"query": "q_dashboard", "sql": B})
    deployed = client.post("/api/workbench/deploy-rewrite",
                           json={"query": "q_dashboard", "sql": B}).get_json()
    assert deployed["ok"] is True and deployed["detail"]["verified"] is True
    st = client.get("/api/state").get_json()["data"]
    assert st["assets"]["rewrite_sql"]["q_dashboard"] == B.strip()


# ---------- 6. 存档兼容：旧存档没有 rewrite_checks 也能部署 ----------

def test_old_save_without_rewrite_checks_still_deploys():
    st = _rich(seed=707)
    data = to_jsonable(st)
    data["lab"].pop("rewrite_checks", None)
    loaded = from_jsonable(GameState, data)
    assert loaded.lab.rewrite_checks == {}

    result = workbench.deploy_rewrite(loaded, "q_dashboard", A)
    assert result["ok"] and result["verified"] is True
    assert loaded.assets.rewrite_sql["q_dashboard"] == A.strip()
