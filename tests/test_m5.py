from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lab import replica, sandbox  # noqa: E402


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    path = tmp_path_factory.mktemp("lab") / "replica.db"
    replica.build(path)
    return path


def test_replica_scale(db) -> None:
    info = replica.info(db)
    assert info["tables"]["orders"] == 200_000
    assert info["tables"]["order_items"] == 600_000
    assert "idx_orders_created" in info["indexes"]


def test_select_returns_plan_and_warning(db) -> None:
    # 考核副本自带 (user_id, created_at) 复合索引：按 id 排序才会额外排序
    sql = "SELECT * FROM orders WHERE user_id = 123 ORDER BY id DESC LIMIT 20"
    r = sandbox.run(sql, db)
    assert r["ok"] and r["row_count"] <= 20
    assert any("TEMP B-TREE" in p["detail"] for p in r["plan"])
    assert any("额外排序" in w for w in r["warnings"])


def test_date_function_kills_index(db) -> None:
    r = sandbox.run("SELECT COUNT(*) FROM orders WHERE DATE(created_at) = '2024-01-01'", db)
    assert r["ok"]
    assert any("函数" in w for w in r["warnings"])


def test_add_index_changes_plan(db) -> None:
    name = "idx_orders_status_created"
    try:
        import sqlite3

        conn = sqlite3.connect(db)
        conn.execute(f"CREATE INDEX IF NOT EXISTS {name} ON orders(status, created_at)")
        conn.commit()
        conn.close()
        r = sandbox.run("SELECT COUNT(*) FROM orders WHERE status = 3", db)
        assert any(name in p["detail"] for p in r["plan"])
        assert any("索引" in w for w in r["warnings"])
    finally:
        import sqlite3

        conn = sqlite3.connect(db)
        conn.execute(f"DROP INDEX IF EXISTS {name}")
        conn.commit()
        conn.close()


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM orders",
        "PRAGMA table_info(orders)",
        "SELECT 1; SELECT 2",
        "ATTACH DATABASE 'x.db' AS x",
        "UPDATE orders SET amount = 0",
    ],
)
def test_sandbox_rejects(sql, db) -> None:
    r = sandbox.run(sql, db)
    assert r["ok"] is False and r["error"]


def test_replica_supports_exam_tasks(db) -> None:
    """考核题要靠这些索引才成立：覆盖索引、区间查询、对账 join。"""
    info = replica.info(db)
    for name in ("idx_orders_user_created", "idx_orders_created", "idx_payments_created"):
        assert name in info["indexes"], name
    r = sandbox.run(
        "SELECT user_id, created_at FROM orders WHERE user_id = 12345 "
        "ORDER BY created_at DESC LIMIT 20",
        db,
    )
    assert r["ok"] and any("COVERING INDEX" in p["detail"] for p in r["plan"])
