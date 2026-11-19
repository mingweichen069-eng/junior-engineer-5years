"""v1.1.3 回归：生产初始索引与玩家新增索引必须被统一识别。

核心验收：玩家不重复部署生产已有的索引，也能在改写后拿到真实收益。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.engine import new_game  # noqa: E402
from core.model.state import DeployedIndex  # noqa: E402
from core.rules import workbench  # noqa: E402
from core.rules.evaluate import evaluate_peak  # noqa: E402

GOOD = workbench.REFERENCE_REWRITES["q_dashboard"]
ORIGINAL = next(q["sql"] for q in workbench.catalog() if q["id"] == "q_dashboard")


@pytest.fixture(autouse=True)
def isolated_lab(tmp_path, monkeypatch):
    monkeypatch.setattr(workbench, "LAB_DB", tmp_path / "lab.db")
    monkeypatch.setattr(workbench, "_roll_incident", lambda *a, **k: False)


def _rich(seed: int = 601):
    st = new_game(seed=seed)
    st.player.ap = 999
    st.player.energy = 999.0
    return st


def _created_index_rows(st):
    return [
        row for row in workbench.production_indexes(st)
        if row["table"] == "orders"
        and [c.lower() for c in row["columns"]] == ["created_at"]
    ]


# ---------- 1. 初始索引存在，但不是玩家资产 ----------

def test_initial_indexes_are_production_state_not_player_assets():
    st = _rich()
    assert st.assets.indexes == []
    rows = {row["name"]: row for row in workbench.production_indexes(st)}
    assert rows["idx_orders_created"]["source"] == "initial"
    assert rows["idx_orders_created"]["columns"] == ["created_at"]
    assert rows["idx_items_order"]["source"] == "initial"
    # 玩家资产里不能凭空出现初始索引
    assert all(row["name"] not in {i.name for i in st.assets.indexes} for row in rows.values())


# ---------- 2. 不部署索引，改写也能吃到初始索引的收益 ----------

def test_rewrite_uses_initial_index_without_deploying_anything():
    st = _rich(seed=602)
    assert "q_dashboard" not in workbench.effective_for(st, "orders", ["created_at"])
    before = evaluate_peak(st).p95_ms

    check = workbench.check_rewrite(st, "q_dashboard", GOOD)
    assert check["ok"] and check["verified"] is True
    assert check["submitted"]["used_index"] is True

    result = workbench.deploy_rewrite(st, "q_dashboard", GOOD)
    assert result["ok"] and result["verified"] is True
    # 关键：没有部署任何索引，资产里也没有伪造的初始索引
    assert st.assets.indexes == []
    assert st.assets.rewrite_sql["q_dashboard"] == GOOD.strip()
    # 生产索引判定与实验库结论一致
    assert "q_dashboard" in workbench.effective_for(st, "orders", ["created_at"])
    assert workbench.mods(st)["per_query"]["q_dashboard"] == pytest.approx(0.55)
    slow = {row["id"]: row for row in workbench.slow_queries(st)}
    assert "idx_orders_created" in slow["q_dashboard"]["helping_indexes"]
    # 生产模拟的 p95 真的下降（不是只改了一个标志）
    assert evaluate_peak(st).p95_ms < before


# ---------- 3. 重复部署初始索引：拒绝、不扣费、不产生资产 ----------

@pytest.mark.parametrize("sql", [
    "CREATE INDEX idx_orders_created ON orders(created_at)",
    "CREATE INDEX ix_same_columns ON orders(created_at)",
])
def test_redeploying_initial_index_is_rejected_without_side_effects(sql):
    st = _rich(seed=603)
    ap0 = st.player.ap
    before = workbench.mods(st)

    result = workbench.deploy_index(st, sql)
    assert result["ok"] is False
    assert result["duplicate"] is True
    assert result["charged"] is False
    assert result["source"] == "initial"
    assert st.player.ap == ap0
    assert st.assets.indexes == []
    assert st.lab.deployments == []
    assert workbench.mods(st)["per_query"] == before["per_query"]
    assert workbench.mods(st)["io_wait"] == before["io_wait"]


# ---------- 4. 旧存档里的重复资产不叠加收益 ----------

def test_legacy_duplicate_index_asset_does_not_double_benefit():
    st = _rich(seed=604)
    assert workbench.deploy_rewrite(st, "q_dashboard", GOOD)["ok"]
    baseline = workbench.mods(st)["per_query"]["q_dashboard"]
    assert baseline == pytest.approx(0.55)

    # 模拟旧版本留下的「重复部署初始索引」资产
    st.assets.indexes.append(DeployedIndex(
        table="orders", columns=["created_at"], name="ix_legacy_created",
        effective_for=["q_dashboard"], week=1,
    ))
    rows = _created_index_rows(st)
    assert len(rows) == 1 and rows[0]["source"] == "initial"   # 去重后只剩初始索引
    assert workbench.mods(st)["per_query"]["q_dashboard"] == pytest.approx(0.55)
    # 但旧资产仍然真实占磁盘 / 写放大，不把代价洗掉
    assert workbench.mods(st)["io_wait"] > 1.0


# ---------- 5. 玩家新增的唯一索引正常产生收益 ----------

def test_player_added_index_still_benefits():
    st = _rich(seed=605)
    result = workbench.deploy_index(st, "CREATE INDEX ix_user ON orders(user_id, created_at)")
    assert result["ok"] and "q_my_orders" in result["effective_for"]
    assert len(st.assets.indexes) == 1
    rows = {row["name"]: row for row in workbench.production_indexes(st)}
    assert rows["ix_user"]["source"] == "player"
    assert workbench.mods(st)["per_query"]["q_my_orders"] == pytest.approx(0.55)


def test_two_useful_indexes_do_not_stack_per_query_benefit():
    st = _rich(seed=606)
    assert workbench.deploy_rewrite(st, "q_dashboard", GOOD)["ok"]
    assert workbench.deploy_index(st, "CREATE INDEX ix_created_user ON orders(created_at, user_id)")["ok"]
    # 两个索引都对 q_dashboard 有效，但一条查询只按一份折减，不叠加 0.55*0.55
    per_query = workbench.mods(st)["per_query"]
    assert per_query["q_dashboard"] == pytest.approx(0.55)
