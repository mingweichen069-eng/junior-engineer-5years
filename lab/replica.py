"""SQLite 副本：给玩家一个真实可查询的数据库。

两套库各司其职，不要混为一谈：
- ``replica.db``（20 万订单）：v0.9 遗留的 SQL 判题主库，供 exam.py 的 SQL 题
  与 tools/scan_load_profile.py 使用；它预置了若干判题用索引，不是生产环境。
- ``workbench.db``（LAB_ORDERS 行）：v1.0 起的工程工作台实验库，由 build_lab()
  秒级重建。它的**初始索引严格来自 content/schema.json**，与生产模拟一致，
  这样玩家在实验库里得到的结论才不会和生产对不上（v1.1.2 阶段四）。

实验库只用来看访问路径与结果一致性，本机 SQLite 耗时不参与生产数值。
"""

from __future__ import annotations

import json
import random
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "lab" / "replica.db"

ORDERS = 200_000
# 实验库规模：固定 4,000 行。够看清执行计划，又能秒级重置。
# 如果哪天要换成 20 万行，只需改这里并在调用处显式传 orders_n。
LAB_ORDERS = 4_000
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
USERS = 30_000
ITEMS_PER_ORDER = 3
PAYMENTS = ORDERS // 3
# 订单集中在最近 90 天：保证"昨天 / 上周"这类区间查询有真实数据
LAUNCH = datetime(2023, 11, 2, 0, 0, 0)
DAYS = 90
ORDERS_PER_DAY = ORDERS // DAYS


def _ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def build(path: Path | None = None) -> Path:
    path = path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        PRAGMA journal_mode = MEMORY;
        PRAGMA synchronous = OFF;
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            phone TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE orders (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            status INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            amount INTEGER NOT NULL
        );
        CREATE TABLE order_items (
            id INTEGER PRIMARY KEY,
            order_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            qty INTEGER NOT NULL,
            price INTEGER NOT NULL
        );
        CREATE TABLE payments (
            id INTEGER PRIMARY KEY,
            order_id INTEGER NOT NULL,
            status INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )
    rnd = random.Random(42)
    users = [
        (i, f"138{rnd.randint(10000000, 99999999)}", _ts(LAUNCH + timedelta(days=i % DAYS)))
        for i in range(1, USERS + 1)
    ]
    conn.executemany("INSERT INTO users VALUES(?,?,?)", users)

    orders = []
    items = []
    payments = []
    item_id = 1
    for i in range(1, ORDERS + 1):
        uid = rnd.randint(1, USERS)
        status = rnd.choices([0, 1, 2, 3, 4], weights=[6, 20, 15, 55, 4])[0]
        day = (i - 1) // ORDERS_PER_DAY
        minute = (i - 1) % ORDERS_PER_DAY
        # 每天均匀铺满，最后一天正好约 3000 单
        created = LAUNCH + timedelta(days=day, seconds=minute * 28 + rnd.randint(0, 27))
        amount = rnd.randint(1500, 30000)
        orders.append((i, uid, status, _ts(created), amount))
        for _ in range(ITEMS_PER_ORDER):
            items.append((item_id, i, rnd.randint(1, 5000), rnd.randint(1, 3), rnd.randint(500, 9999)))
            item_id += 1
        if i % 3 == 0:
            payments.append((len(payments) + 1, i, rnd.randint(1, 3), _ts(created + timedelta(minutes=2))))
    # 故意造 60 条重复流水：支付回调重试写重的真实形态，对账题要用
    for order_id in rnd.sample(range(1, ORDERS + 1), 60):
        payments.append((len(payments) + 1, order_id, 2, _ts(LAUNCH + timedelta(days=1))))
    conn.executemany("INSERT INTO orders VALUES(?,?,?,?,?)", orders)
    conn.executemany("INSERT INTO order_items VALUES(?,?,?,?,?)", items)
    conn.executemany("INSERT INTO payments VALUES(?,?,?,?)", payments)

    conn.executescript(
        """
        CREATE INDEX idx_orders_created ON orders(created_at);
        CREATE INDEX idx_orders_user_created ON orders(user_id, created_at);
        CREATE INDEX idx_items_order ON order_items(order_id);
        CREATE INDEX idx_payments_order ON payments(order_id);
        CREATE INDEX idx_payments_created ON payments(created_at);
        ANALYZE;
        """
    )
    conn.commit()
    conn.close()
    return path


def production_indexes() -> list[tuple[str, str, list[str]]]:
    """生产模拟的初始索引，直接从 content/schema.json 读。

    实验库必须只建这些索引：多建一个「玩家还没部署」的索引，就会让实验结论
    与生产不符（v1.1.2 阶段四修掉的正是 idx_orders_user_created 这类预置索引）。
    """
    data = json.loads((ROOT / "content" / "schema.json").read_text(encoding="utf-8"))
    out: list[tuple[str, str, list[str]]] = []
    for table, spec in data["tables"].items():
        for name, idx in (spec.get("indexes") or {}).items():
            cols = list(idx.get("columns") or [])
            if not all(_IDENT.match(x) for x in [name, table, *cols]):
                raise ValueError(f"schema.json 里有非法索引标识符：{name}/{table}/{cols}")
            out.append((name, table, cols))
    return out


# 判题主库（replica.db）的索引清单。它服务于历史 SQL 题库，不代表生产 schema；
# 工程工作台的实验库走上面的 production_indexes()，不走这里。
REQUIRED_INDEXES = {
    "idx_orders_created": ("orders", "created_at"),
    "idx_orders_user_created": ("orders", "user_id, created_at"),
    "idx_items_order": ("order_items", "order_id"),
    "idx_payments_order": ("payments", "order_id"),
    "idx_payments_created": ("payments", "created_at"),
}


def build_lab(path: Path | None = None, orders_n: int = LAB_ORDERS) -> Path:
    """实验库：与生产初始 schema 一致，只放几千行以便秒级重置。

    表结构与 ``content/schema.json`` 同构，索引集合由 ``production_indexes()``
    从同一份 schema 生成——不会预置生产里还不存在的索引。EXPLAIN QUERY PLAN
    关心的是「索引在不在、谓词能不能命中」，与表是 4,000 行还是 120 万行无关；
    但**耗时**会随规模变化，所以实验库耗时只能作本机参考，不能当生产指标。
    """
    path = path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    # 不删文件：在同一个库里 DROP + 重建表，避免删文件被锁 / 被安全策略拦。
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        PRAGMA journal_mode = MEMORY;
        PRAGMA synchronous = OFF;
        DROP TABLE IF EXISTS users;
        DROP TABLE IF EXISTS orders;
        DROP TABLE IF EXISTS order_items;
        DROP TABLE IF EXISTS payments;
        CREATE TABLE users (id INTEGER PRIMARY KEY, phone TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE orders (
            id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, status INTEGER NOT NULL,
            created_at TEXT NOT NULL, amount INTEGER NOT NULL
        );
        CREATE TABLE order_items (
            id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL, product_id INTEGER NOT NULL,
            qty INTEGER NOT NULL, price INTEGER NOT NULL
        );
        CREATE TABLE payments (
            id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL, status INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )
    rnd = random.Random(7)
    users = [(i, f"138{rnd.randint(10000000, 99999999)}", _ts(LAUNCH + timedelta(days=i % 90)))
             for i in range(1, max(100, orders_n // 8) + 1)]
    conn.executemany("INSERT INTO users VALUES(?,?,?)", users)
    orders, items, payments = [], [], []
    item_id = 1
    for i in range(1, orders_n + 1):
        uid = rnd.randint(1, len(users))
        status = rnd.choices([0, 1, 2, 3, 4], weights=[6, 20, 15, 55, 4])[0]
        created = LAUNCH + timedelta(days=(i - 1) % 90, seconds=(i * 7) % 86400)
        orders.append((i, uid, status, _ts(created), rnd.randint(1500, 30000)))
        for _ in range(ITEMS_PER_ORDER):
            items.append((item_id, i, rnd.randint(1, 5000), rnd.randint(1, 3), rnd.randint(500, 9999)))
            item_id += 1
        if i % 3 == 0:
            payments.append((len(payments) + 1, i, rnd.randint(1, 3), _ts(created + timedelta(minutes=2))))
    conn.executemany("INSERT INTO orders VALUES(?,?,?,?,?)", orders)
    conn.executemany("INSERT INTO order_items VALUES(?,?,?,?,?)", items)
    conn.executemany("INSERT INTO payments VALUES(?,?,?,?)", payments)
    # 只建生产初始 schema 里真实存在的索引。玩家自己建的索引由实验台执行，
    # 部署到生产的索引由 CompanyAssets 记录——实验库不会替玩家「先建好」。
    for name, table, cols in production_indexes():
        conn.execute(f"CREATE INDEX {name} ON {table}({', '.join(cols)})")
    conn.execute("ANALYZE")
    conn.commit()
    conn.close()
    return path


def ensure(path: Path | None = None) -> Path:
    """副本存在就直接用；缺索引的老副本就地补齐（不重建 20 万行）。"""
    path = path or DB_PATH
    if not path.exists():
        build(path)
        return path
    conn = sqlite3.connect(path)
    try:
        missing = []
        for name, (table, cols) in REQUIRED_INDEXES.items():
            has = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?", (name,)
            ).fetchone()
            if not has:
                missing.append((name, table, cols))
        # 索引齐全时只做 SELECT，不写、不 commit——实验库才不会被无谓地上写锁。
        if missing:
            for name, table, cols in missing:
                conn.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table}({cols})")
            conn.commit()
    except sqlite3.Error:
        conn.close()
        return build(path)
    finally:
        try:
            conn.close()
        except sqlite3.Error:
            pass
    return path


def add_index(name: str, columns: list[str], path: Path | None = None) -> bool:
    """实验台里的索引变更（立即生效，方便玩家马上验证）。"""
    path = ensure(path)
    conn = sqlite3.connect(path)
    try:
        conn.execute(f"CREATE INDEX IF NOT EXISTS {name} ON orders({', '.join(columns)})")
        conn.commit()
        return True
    except sqlite3.Error:
        return False
    finally:
        conn.close()


def info(path: Path | None = None) -> dict:
    path = ensure(path)
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        tables = {}
        for name in ("orders", "order_items", "users", "payments"):
            n = conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
            tables[name] = n
        indexes = []
        for name in ("orders", "order_items", "payments", "users"):
            indexes += [r[1] for r in conn.execute(f"PRAGMA index_list('{name}')")]
        return {
            "path": str(path),
            "tables": tables,
            "indexes": indexes,
            "size_mb": round(path.stat().st_size / 1024 / 1024, 1),
        }
    finally:
        conn.close()
