from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.engine import new_game, step  # noqa: E402
from core.model.serde import from_jsonable, to_jsonable  # noqa: E402
from core.model.state import GameState, Index  # noqa: E402
from core.rules import constants as C  # noqa: E402
from core.rules.evaluate import evaluate_peak  # noqa: E402
from core.rules.queue import disk_wait_ms, mmc_p95  # noqa: E402
from core.rules.storage import buffer_hit, disk_used_gb, total_pages  # noqa: E402
from core.rules.traffic import traffic  # noqa: E402
from services import save as saves  # noqa: E402


@pytest.fixture()
def state() -> GameState:
    return new_game(seed=42)


def test_traffic_matches_design(state: GameState) -> None:
    t = traffic(state.company.registered)
    assert round(t.dau) == 23_994
    assert round(t.peak_qps, 1) == 78.0


def test_storage_matches_design(state: GameState) -> None:
    assert total_pages(state.db) == 105_148
    assert round(disk_used_gb(state.db), 2) == 1.60
    assert round(buffer_hit(state.db), 3) == 0.810


def test_initial_peak_matches_design(state: GameState) -> None:
    m = evaluate_peak(state)
    dash = m.by_id("q_dashboard")
    orders = m.by_id("q_my_orders")
    assert dash is not None and orders is not None
    assert 840 <= dash.time_ms <= 870          # 设计稿 852ms
    assert orders.path == "index_order"        # 走错索引，全索引扫描
    assert orders.rows == 1_200_000
    assert 1.0 <= orders.qps <= 1.3            # 1.17 QPS，与文档一致
    # 主键查询走聚簇索引，不该再是全表扫描
    detail = m.by_id("q_detail")
    assert detail is not None and detail.path == "pk"
    assert 0.70 <= m.io_util <= 0.80
    assert 120 <= m.p95_ms <= 200


def test_metrics_are_physically_consistent(state: GameState) -> None:
    m = evaluate_peak(state)
    assert 0 <= m.io_util <= 1.0
    assert 0 <= m.api_rho <= 1.0
    assert m.p95_ms < 60_000                    # 不把哨兵值当 p95
    if m.p95_ms >= 5_000:
        assert m.error_rate >= 0.10


def test_composite_index_fixes_orders_query(state: GameState) -> None:
    before = evaluate_peak(state)
    disk_before = disk_used_gb(state.db)

    state.db.tables["orders"].indexes["idx_user_created"] = Index(
        columns=["user_id", "created_at"], entry_bytes=26
    )
    after = evaluate_peak(state)

    q_before = before.by_id("q_my_orders")
    q_after = after.by_id("q_my_orders")
    assert q_before and q_after
    assert q_after.time_ms < 15
    assert q_after.time_ms < q_before.time_ms
    assert q_after.rows <= 20
    assert q_after.path == "index_eq"
    # 索引不是白拿的：磁盘变大
    assert disk_used_gb(state.db) > disk_before


def test_write_cost_grows_with_indexes(state: GameState) -> None:
    from core.rules.query import choose_path

    table = state.db.tables["orders"]
    q = {"kind": "write"}
    before = choose_path(table, q, 0.8).cost.total_ms
    table.indexes["idx_user_created"] = Index(columns=["user_id", "created_at"])
    after = choose_path(table, q, 0.8).cost.total_ms
    assert after == pytest.approx(before + C.C_INDEX_INSERT_MS)


def test_determinism(state: GameState) -> None:
    # 同一输入的两个全新存档，推进 6 周后指标逐项一致
    a = new_game(seed=7)
    b = new_game(seed=7)
    for _ in range(6):
        ma = step(a)
        mb = step(b)
        assert (ma.week, ma.p95_ms, ma.io_iops) == (mb.week, mb.p95_ms, mb.io_iops)
    # 序列化再反序列化不改变状态
    restored = from_jsonable(GameState, to_jsonable(a))
    assert to_jsonable(restored) == to_jsonable(a)


def test_save_load_roundtrip(state: GameState, tmp_path: Path) -> None:
    db = tmp_path / "saves.db"
    step(state)
    saves.save_game("t1", state, path=db)
    loaded = saves.load_game("t1", path=db)
    assert loaded is not None
    assert to_jsonable(loaded) == to_jsonable(state)


def test_queue_saturation() -> None:
    saturated = mmc_p95(150, 0.08, 8)
    healthy = mmc_p95(150, 0.08, 40)
    assert saturated["rho"] >= 1
    assert healthy["rho"] < 1
    assert healthy["wait_p95_ms"] < 50
    assert disk_wait_ms(1500) < 5
    assert disk_wait_ms(2999) > 10
