"""v0.3 新增机制的回归测试。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.engine import new_game, step  # noqa: E402
from core.rules import actions as ar  # noqa: E402
from core.rules import debt, diagnose, economy  # noqa: E402
from core.rules import load_profile  # noqa: E402
from core.rules.evaluate import evaluate_peak  # noqa: E402


# ---------- 1. 负载权重只有一个数据源 ----------

def test_load_profile_single_source() -> None:
    import json

    data = json.loads(load_profile.PROFILE_PATH.read_text(encoding="utf-8"))
    assert data == load_profile.DEFAULT_WEIGHTS


def test_my_orders_qps_matches_docs() -> None:
    st = new_game(seed=42)
    m = evaluate_peak(st)
    q = m.by_id("q_my_orders")
    assert q is not None
    assert 1.0 <= q.qps <= 1.3


# ---------- 2. 索引碎片真的会累积 ----------

def test_bloat_accumulates_and_optimize_clears_it() -> None:
    st = new_game(seed=7)
    st.flags["writes_enabled"] = True
    for _ in range(6):
        step(st)
    idx = st.db.tables["orders"].indexes["idx_orders_created"]
    assert idx.bloat > 0
    before = idx.bloat
    # 优化索引动作会把它清零
    st.player.ap = 5
    ok, _ = ar.start(st, "optimize_index")
    assert ok
    step(st)
    assert st.db.tables["orders"].indexes["idx_orders_created"].bloat < before


# ---------- 3. 可重复动作不能白点 ----------

def test_pool_grows_each_time() -> None:
    st = new_game(seed=8)
    ar.start(st, "pool_60")
    first = st.app.pool_size
    st.player.ap = 5
    ar.start(st, "pool_60")
    assert st.app.pool_size == first + 20


def test_rewrite_is_one_shot(exam_pass) -> None:
    st = new_game(seed=9, difficulty="veteran")
    assert exam_pass(st, "db_t2")[0]
    assert ar.start(st, "rewrite_dashboard")[0]
    ok, why = ar.start(st, "rewrite_dashboard")
    assert not ok and "做过" in why


# ---------- 4. 瓶颈标签 ----------

def test_bottleneck_labels_scan_at_start() -> None:
    st = new_game(seed=10)
    m = evaluate_peak(st)
    d = diagnose.diagnose(st, m)
    assert d["code"] in {"db_scan", "disk"}
    assert d["hint"]


def test_bottleneck_says_capacity_when_queries_are_optimized() -> None:
    from core.rules.evaluate import PeakMetrics, QueryMetric

    st = new_game(seed=11)
    metrics = PeakMetrics(
        week=12, dau=50000, avg_qps=20, peak_qps=120, buffer_hit=0.9,
        disk_used_gb=1.6, io_iops=2900, io_util=0.97, disk_wait_ms=8,
        queries=[
            QueryMetric("q_my_orders", "我的订单", qps=5, time_ms=40, rows=20,
                        path="index_eq", io_ops=5),
            QueryMetric("q_dashboard", "运营大屏", qps=3, time_ms=30, rows=3000,
                        path="index_eq", io_ops=3),
        ],
        p95_ms=600,
    )
    d = diagnose.diagnose(st, metrics)
    assert d["code"] == "capacity"
    assert "查询优化收益有限" in d["hint"]


# ---------- 5. SLA 阶梯 ----------

def test_sla_tiers_grow_with_severity() -> None:
    tiers = [economy.sla_outcome(a)[0] for a in (0.999, 0.992, 0.985, 0.95)]
    assert tiers[0] == 0
    assert tiers == sorted(tiers)
    assert len(set(tiers)) == 4


# ---------- 6. 锁冲击 & 在线 DDL ----------

def test_naive_ddl_causes_lock_spike() -> None:
    st = new_game(seed=12)
    ar.start(st, "add_index_user_created")
    step(st)                      # 生效当周
    assert st.history[-1].p95_ms >= 1000
    assert st.flags["lock_week"] == st.meta.week


# ---------- 7. 流量毛刺 ----------

def test_traffic_spike_happens_some_weeks() -> None:
    st = new_game(seed=3)
    spikes = 0
    for _ in range(24):
        step(st)
        if st.flags.get("spike_mult", 1.0) > 1.0:
            spikes += 1
    assert spikes >= 2            # 随机小活动 + 第 12~13 周大促


# ---------- 8. 技术债分类 ----------

def test_debt_categories() -> None:
    st = new_game(seed=14)
    cats = debt.breakdown(st)
    assert set(cats) == {"query", "ops", "arch"}
    assert cats["query"] > 0 and cats["ops"] > 0 and cats["arch"] > 0
    assert 0 <= debt.pressure(st) <= 1


# ---------- 9. 沙盘 ----------

def test_sandbox_never_ends_and_has_infinite_ap() -> None:
    st = new_game(seed=15, sandbox=True)
    for _ in range(20):
        step(st)
    assert st.ending.ended is None
    assert st.player.ap >= 5


# ---------- 10. 快照回退（API） ----------

def test_snapshot_and_rollback(client) -> None:
    client.post("/api/new", json={"seed": 16})
    client.post("/api/action", json={"id": "cache_dashboard"})
    snaps = client.get("/api/snapshots").get_json()["data"]
    assert snaps, "动作后应该自动生成快照"
    sid = snaps[0]["id"]
    # 推进后回退
    client.post("/api/advance", json={"weeks": 2})
    d = client.post("/api/rollback", json={"id": sid}).get_json()["data"]
    assert d["week"] == 1
    assert "q_dashboard" not in d["cache"]["policies"]   # 回到加缓存之前
