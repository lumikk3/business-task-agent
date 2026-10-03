"""SQLite-backed business data store (V1 mock backend).

Tables follow DESIGN.md section 8. The store is seeded with demo data so the
agent can run the six core scenarios out of the box. Swap this module for a
real PostgreSQL DAO in V2 without touching the tool layer.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
import threading
from datetime import date
from pathlib import Path
from typing import Protocol

# Fixed "today" for the demo so policy checks (7-day / 15-day windows) are
# deterministic in tests and demos. Override with AGENT_DEMO_TODAY=YYYY-MM-DD.
DEMO_TODAY = date(2026, 9, 27)

# Default location of the generated "fake enterprise business system"
# (built by scripts/generate_data.py). Override with AGENT_DB_PATH.
DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "data" / "business.db"


def demo_today() -> date:
    raw = os.environ.get("AGENT_DEMO_TODAY")
    if raw:
        try:
            return date.fromisoformat(raw)
        except ValueError:
            pass
    return DEMO_TODAY


_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS products (
    product_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    price REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    delivered_at TEXT,
    total REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS order_items (
    order_id TEXT NOT NULL,
    product_id TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    price REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS logistics (
    order_id TEXT PRIMARY KEY,
    carrier TEXT,
    tracking_no TEXT,
    status TEXT NOT NULL,
    updated_at TEXT,
    location TEXT
);
CREATE TABLE IF NOT EXISTS after_sale_requests (
    request_id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL,
    type TEXT NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS refunds (
    refund_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    order_id TEXT NOT NULL,
    amount REAL NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tickets (
    ticket_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    order_id TEXT,
    reason TEXT NOT NULL,
    recommended_action TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

# Public alias so the data generator can create the same tables in a fresh DB.
SCHEMA = _SCHEMA


class BusinessStore(Protocol):
    """业务数据层契约（Step 18）。

    ``Store``（SQLite）与 ``PgStore``（PostgreSQL）都实现这一组方法，所以
    ``BusinessTools`` / 工具层 / Agent 不关心底层是哪个数据库。
    """

    def find_orders(self, user_id: str, order_id: str | None = None) -> list[dict]: ...

    def get_order(self, order_id: str) -> dict | None: ...

    def get_order_items(self, order_id: str) -> list[dict]: ...

    def get_logistics(self, order_id: str) -> dict | None: ...

    def get_after_sale(self, order_id: str) -> list[dict]: ...

    def get_refunds(self, order_id: str) -> list[dict]: ...

    def create_return_request(self, order_id: str, reason: str) -> dict: ...

    def create_refund_request(self, order_id: str, amount: float) -> dict: ...

    def create_ticket(self, user_id: str, order_id: str | None, reason: str,
                      recommended_action: str | None) -> dict: ...

    def counts(self) -> dict[str, int]: ...


# 业务表（SQLite 与 PostgreSQL 两侧同名同序，迁移脚本也依赖这个顺序）
BUSINESS_TABLES = ("users", "products", "orders", "order_items", "logistics",
                   "after_sale_requests", "refunds", "tickets")

_SEED_USERS = [
    ("U10001", "张三"),
    ("U10002", "李四"),
]

_SEED_PRODUCTS = [
    ("P001", "无线耳机", "耳机", 299.0),
    ("P002", "机械键盘", "数码配件", 459.0),
    ("P003", "咖啡豆礼盒", "食品", 128.0),
    ("P004", "降噪耳机", "耳机", 1299.0),
]

# order_id, user_id, status, created_at, delivered_at, total
_SEED_ORDERS = [
    ("O202609001", "U10001", "delivered", "2026-09-20", "2026-09-23", 299.0),
    ("O202609002", "U10001", "delivered", "2026-08-20", "2026-08-25", 459.0),
    ("O202609003", "U10002", "delivered", "2026-08-05", "2026-08-10", 1299.0),
    ("O202609004", "U10002", "shipped", "2026-09-25", None, 128.0),
    ("O202609005", "U10001", "paid", "2026-09-26", None, 128.0),
]

_SEED_ITEMS = [
    ("O202609001", "P001", 1, 299.0),
    ("O202609002", "P002", 1, 459.0),
    ("O202609003", "P004", 1, 1299.0),
    ("O202609004", "P003", 1, 128.0),
    ("O202609005", "P003", 1, 128.0),
]

# order_id, carrier, tracking_no, status, updated_at, location
_SEED_LOGISTICS = [
    ("O202609001", "顺丰", "SF1234567890", "已签收", "2026-09-23 18:30", "上海浦东"),
    ("O202609002", "顺丰", "SF0987654321", "已签收", "2026-08-25 14:10", "上海徐汇"),
    ("O202609003", "圆通", "YT5566778899", "已签收", "2026-08-10 11:00", "北京朝阳"),
    ("O202609004", "圆通", "YT1122334455", "运输中", "2026-09-26 09:00", "杭州转运中心"),
    ("O202609005", None, None, "待发货", None, None),
]

# request_id, order_id, type, reason, status, created_at
_SEED_AFTER_SALE = [
    ("R202609001", "O202609002", "退货", "按键失灵,质量问题", "returned", "2026-09-10"),
]

# refund_id, request_id, order_id, amount, status, created_at
_SEED_REFUNDS = [
    ("RF202609001", "R202609001", "O202609002", 459.0, "processing", "2026-09-20"),
]


class Store:
    """Thread-safe SQLite store (in-memory by default, file path optional).

    ``seed=True`` loads the small hand-written demo rows (2 users / 5 orders)
    used by the unit tests and the six-scenario demo. ``seed=False`` creates an
    empty schema only — that is how the generated business DB is opened.
    """

    def __init__(self, path: str = ":memory:", seed: bool = True):
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._init(seed)

    def _init(self, seed: bool = True) -> None:
        with self._lock:
            self._conn.executescript(_SCHEMA)
            if not seed:
                self._conn.commit()
                return
            self._conn.executemany("INSERT OR IGNORE INTO users VALUES (?, ?)", _SEED_USERS)
            self._conn.executemany(
                "INSERT OR IGNORE INTO products VALUES (?, ?, ?, ?)", _SEED_PRODUCTS
            )
            self._conn.executemany(
                "INSERT OR IGNORE INTO orders VALUES (?, ?, ?, ?, ?, ?)", _SEED_ORDERS
            )
            self._conn.executemany(
                "INSERT OR IGNORE INTO order_items VALUES (?, ?, ?, ?)", _SEED_ITEMS
            )
            self._conn.executemany(
                "INSERT OR IGNORE INTO logistics VALUES (?, ?, ?, ?, ?, ?)", _SEED_LOGISTICS
            )
            self._conn.executemany(
                "INSERT OR IGNORE INTO after_sale_requests VALUES (?, ?, ?, ?, ?, ?)",
                _SEED_AFTER_SALE,
            )
            self._conn.executemany(
                "INSERT OR IGNORE INTO refunds VALUES (?, ?, ?, ?, ?, ?)", _SEED_REFUNDS
            )
            self._conn.commit()

    def _query(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def _insert(self, sql: str, params: tuple) -> None:
        with self._lock:
            self._conn.execute(sql, params)
            self._conn.commit()

    # ---- queries -------------------------------------------------------
    def find_orders(self, user_id: str, order_id: str | None = None) -> list[dict]:
        if order_id:
            return self._query(
                "SELECT * FROM orders WHERE user_id = ? AND order_id = ?", (user_id, order_id)
            )
        return self._query("SELECT * FROM orders WHERE user_id = ? ORDER BY created_at DESC", (user_id,))

    def get_order(self, order_id: str) -> dict | None:
        rows = self._query("SELECT * FROM orders WHERE order_id = ?", (order_id,))
        return rows[0] if rows else None

    def get_order_items(self, order_id: str) -> list[dict]:
        rows = self._query(
            "SELECT oi.*, p.name AS product_name, p.category FROM order_items oi "
            "JOIN products p ON p.product_id = oi.product_id WHERE oi.order_id = ?",
            (order_id,),
        )
        return rows

    def get_logistics(self, order_id: str) -> dict | None:
        rows = self._query("SELECT * FROM logistics WHERE order_id = ?", (order_id,))
        return rows[0] if rows else None

    def get_after_sale(self, order_id: str) -> list[dict]:
        return self._query(
            "SELECT * FROM after_sale_requests WHERE order_id = ?", (order_id,)
        )

    def get_refunds(self, order_id: str) -> list[dict]:
        return self._query("SELECT * FROM refunds WHERE order_id = ?", (order_id,))

    def counts(self) -> dict[str, int]:
        return {table: len(self._query(f"SELECT 1 FROM {table}"))
                for table in BUSINESS_TABLES}

    # ---- mutations -----------------------------------------------------
    def create_return_request(self, order_id: str, reason: str) -> dict:
        request_id = f"R{demo_today().strftime('%Y%m%d')}{len(self.get_after_sale(order_id)) + 1:03d}"
        self._insert(
            "INSERT INTO after_sale_requests VALUES (?, ?, ?, ?, ?, ?)",
            (request_id, order_id, "退货", reason, "created", demo_today().isoformat()),
        )
        return {"request_id": request_id, "order_id": order_id, "type": "退货",
                "reason": reason, "status": "created"}

    def create_refund_request(self, order_id: str, amount: float) -> dict:
        refund_id = f"RF{demo_today().strftime('%Y%m%d')}{len(self.get_refunds(order_id)) + 1:03d}"
        self._insert(
            "INSERT INTO refunds VALUES (?, ?, ?, ?, ?, ?)",
            (refund_id, "R-PENDING", order_id, amount, "created", demo_today().isoformat()),
        )
        return {"refund_id": refund_id, "order_id": order_id, "amount": amount,
                "status": "created"}

    def create_ticket(self, user_id: str, order_id: str | None, reason: str,
                      recommended_action: str | None) -> dict:
        ticket_id = f"T{demo_today().strftime('%Y%m%d')}0001"
        # keep ticket ids unique in the demo store
        existing = self._query("SELECT ticket_id FROM tickets WHERE ticket_id = ?", (ticket_id,))
        if existing:
            ticket_id = f"T{demo_today().strftime('%Y%m%d')}{len(self._query('SELECT 1 FROM tickets')) + 1:04d}"
        self._insert(
            "INSERT INTO tickets VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ticket_id, user_id, order_id, reason, recommended_action, "open",
             demo_today().isoformat()),
        )
        return {"ticket_id": ticket_id, "user_id": user_id, "order_id": order_id,
                "reason": reason, "recommended_action": recommended_action, "status": "open"}


_default_store: Store | None = None
_default_lock = threading.Lock()


def get_store() -> Store:
    """Process-wide default store (fresh in-memory DB with seed data)."""
    global _default_store
    with _default_lock:
        if _default_store is None:
            _default_store = Store()
        return _default_store


def reset_store() -> Store:
    global _default_store
    with _default_lock:
        _default_store = Store()
        return _default_store


def open_store(path: str | None = None, seed: bool = False) -> Store:
    """Open the generated business DB (the "fake enterprise system").

    Path resolution: explicit argument > ``AGENT_DB_PATH`` env var >
    ``data/business.db``. Build it first with ``scripts/generate_data.py``.
    """
    resolved = path or os.environ.get("AGENT_DB_PATH") or str(DEFAULT_DB_PATH)
    return Store(resolved, seed=seed)


def open_scratch_store(path: str | None = None, seed: bool = False) -> Store:
    """在生成业务库的**临时副本**上操作。

    demo / 脚本会创建退货申请、工单等写操作；直接用 open_store() 会写脏被提交的
    ``data/business.db``，使重复运行结果不一致。这里复制一份到临时目录再打开，
    保证每次运行都从同一份干净数据开始。
    """
    source = path or os.environ.get("AGENT_DB_PATH") or str(DEFAULT_DB_PATH)
    scratch = Path(tempfile.mkdtemp(prefix="agent-db-")) / "business.db"
    shutil.copyfile(source, scratch)
    return Store(str(scratch), seed=seed)
