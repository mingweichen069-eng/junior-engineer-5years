"""v1.1.2 阶段四回归：实验库规模与生产 schema 一致。

覆盖任务书第四节：
- build_lab 默认 4,000 行，前端 / README / 事件线索不再写成 20 万行；
- 实验库初始索引严格来自 content/schema.json，不预置生产里没有的索引；
- 老实验库里残留的预置索引会被就地重建，玩家自己建的索引不受影响；
- 不同数据规模下执行计划判定一致，实验库耗时不参与生产数值。

任务书第九节 12（重置实验库不动生产资产）由 test_v11_deploy_cost 覆盖。
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.engine import new_game  # noqa: E402
from core.rules import workbench  # noqa: E402
from lab import replica, sandbox  # noqa: E402


def _index_names(path: Path) -> set[str]:
    con = sqlite3.connect(path)
    try:
        return {
            r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
            )
        }
    finally:
        con.close()


@pytest.fixture(autouse=True)
def isolated_lab(tmp_path, monkeypatch):
    monkeypatch.setattr(workbench, "LAB_DB", tmp_path / "lab.db")


# ---------- 1. 初始索引与生产 schema 一致 ----------

def test_production_indexes_come_from_schema_json():
    idx = replica.production_indexes()
    names = {name for name, _, _ in idx}
    assert names == {"idx_orders_created", "idx_items_order"}
    assert "idx_orders_user_created" not in names   # 旧版预置、生产里并不存在
    assert "idx_payments_created" not in names
    assert dict((n, (t, c)) for n, t, c in idx)["idx_orders_created"] == ("orders", ["created_at"])


def test_build_lab_creates_only_production_indexes_and_4000_rows():
    p = workbench.LAB_DB
    replica.build_lab(p, orders_n=replica.LAB_ORDERS)
    assert replica.LAB_ORDERS == 4_000
    assert _index_names(p) == {n for n, _, _ in replica.production_indexes()}
    con = sqlite3.connect(p)
    try:
        assert con.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 4_000
    finally:
        con.close()


def test_my_orders_query_has_no_free_index_in_lab():
    """生产里没有 user_id 索引，实验库就不能先替玩家建好，否则结论会骗人。"""
    p = workbench.LAB_DB
    replica.build_lab(p)
    r = sandbox.run(
        "SELECT * FROM orders WHERE user_id = 1 ORDER BY created_at DESC LIMIT 20",
        path=p, prepared=True,
    )
    assert r["ok"]
    assert "idx_orders_user_created" not in " ".join(
        str(x.get("detail", "")) for x in r["plan"]
    )


# ---------- 2. 老实验库残留 / 玩家自建索引 ----------

def test_ensure_lab_rebuilds_when_legacy_preset_index_is_present():
    p = workbench.LAB_DB
    replica.build_lab(p)
    con = sqlite3.connect(p)
    con.execute("CREATE INDEX idx_orders_user_created ON orders(user_id, created_at)")
    con.commit()
    con.close()
    assert "idx_orders_user_created" in _index_names(p)

    workbench.ensure_lab()
    assert "idx_orders_user_created" not in _index_names(p)
    assert _index_names(p) == {n for n, _, _ in replica.production_indexes()}


def test_ensure_lab_keeps_player_created_experiment_indexes():
    p = workbench.LAB_DB
    replica.build_lab(p)
    con = sqlite3.connect(p)
    con.execute("CREATE INDEX ix_player_probe ON orders(status)")
    con.commit()
    con.close()

    workbench.ensure_lab()
    assert "ix_player_probe" in _index_names(p), "不能把玩家在实验库里建的索引顺手清掉"


def test_reset_lab_restores_only_production_indexes():
    p = workbench.LAB_DB
    replica.build_lab(p)
    con = sqlite3.connect(p)
    con.execute("CREATE INDEX ix_player_probe ON orders(status)")
    con.commit()
    con.close()
    workbench.reset_lab()
    assert _index_names(p) == {n for n, _, _ in replica.production_indexes()}
    assert "ix_player_probe" not in _index_names(p)


# ---------- 3. 阶段三回归：去掉预置索引后改写判定仍成立 ----------

def test_rewrite_verification_still_works_after_schema_alignment():
    st = new_game(seed=501)
    st.player.ap, st.player.energy = 999, 999.0
    # v1.1.3：初始 schema 已有 created_at 索引，不需要玩家重复建一份。
    r = workbench.deploy_rewrite(st, "q_dashboard", workbench.REFERENCE_REWRITES["q_dashboard"])
    assert r["ok"] and r["verified"] is True
    assert "q_dashboard" in workbench.mods(st)["per_query"]
    # 实验库耗时只是一个展示字段，不进生产修正
    assert "elapsed_ms" not in workbench.mods(st)
    assert "lab_elapsed_ms" not in workbench.mods(st)


# ---------- 4. 不同规模：计划一致，耗时只作参考 ----------

def test_plan_judgement_is_stable_across_data_scales():
    spec = importlib.util.spec_from_file_location("scan_lab_scale", ROOT / "tools" / "scan_lab_scale.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    rows = mod.scale_report([4_000, 12_000])
    assert len(rows) == 2
    for r in rows:
        assert r["indexes_ok"], r
        assert r["orig_used_index"] is False
        assert r["new_used_index"] is True
        assert r["orig_rows"] == r["new_rows"] > 0
    # 耗时随规模变化是允许的，但它绝不写回任何生产数值（上面已断言 mods 不含它）
    assert all(isinstance(r["orig_ms"], (int, float)) for r in rows)


# ---------- 5. 文案不再误导 ----------

def test_ui_and_docs_no_longer_claim_200k_lab():
    js = (ROOT / "web" / "static" / "app.js").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    events = (ROOT / "content" / "events.json").read_text(encoding="utf-8")
    assert "20 万行副本" not in js
    assert "4,000 行轻量副本" in js
    assert "20 万行副本的轻量同构拷贝" not in readme
    assert "4,000 行轻量副本" in readme
    assert "约 20 万行" not in events
