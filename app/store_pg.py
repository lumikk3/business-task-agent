"""PostgreSQL 数据层（Step 18）。

与 ``app/store.py::Store`` **同方法签名、同返回值形状、同 ID 规则**，因此
``BusinessTools`` / 工具层 / Agent 完全不用改，只换一个 store 实例。

为什么返回值和 SQLite 版长得一样：SQLite 把日期存成文本、金额存成 REAL，
上层（brain / tools）直接当字符串和 float 用。psycopg 会返回 ``date`` /
``Decimal``，所以这里统一归一化成 ISO 字符串和 float，避免上层出现
「Decimal 不能 JSON 序列化」「date 不能做字符串比较」这类问题。

依赖是惰性导入的：没装 ``psycopg`` 时 import 本模块不会报错，只有真正
``PgStore()`` 时才提示安装。

本机验证方式见 DEPLOY.md（`scripts/dev_services.sh` 免 root 起真实 PostgreSQL）。
"""
from __future__ import annotations

import os
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.store import demo_today

DDL = """
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS products (
    product_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    price NUMERIC(12,2) NOT NULL
);
CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    status TEXT NOT NULL,
    created_at DATE NOT NULL,
    delivered_at DATE,
    total NUMERIC(12,2) NOT NULL
);
CREATE TABLE IF NOT EXISTS order_items (
    order_id TEXT NOT NULL REFERENCES orders(order_id),
    product_id TEXT NOT NULL REFERENCES products(product_id),
    quantity INTEGER NOT NULL,
    price NUMERIC(12,2) NOT NULL,
    PRIMARY KEY (order_id, product_id)
);
CREATE TABLE IF NOT EXISTS logistics (
    order_id TEXT PRIMARY KEY REFERENCES orders(order_id),
    carrier TEXT,
    tracking_no TEXT,
    status TEXT NOT NULL,
    updated_at TIMESTAMP,
    location TEXT
);
CREATE TABLE IF NOT EXISTS after_sale_requests (
    request_id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL REFERENCES orders(order_id),
    type TEXT NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at DATE NOT NULL
);
CREATE TABLE IF NOT EXISTS refunds (
    refund_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    order_id TEXT NOT NULL REFERENCES orders(order_id),
    amount NUMERIC(12,2) NOT NULL,
    status TEXT NOT NULL,
    created_at DATE NOT NULL
);
CREATE TABLE IF NOT EXISTS tickets (
    ticket_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    order_id TEXT,
    reason TEXT NOT NULL,
    recommended_action TEXT,
    status TEXT NOT NULL,
    created_at DATE NOT NULL
);
-- Agent 侧的表（DESIGN.md 第 8 节）
CREATE TABLE IF NOT EXISTS agent_tasks (
    task_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    user_id TEXT,
    status TEXT NOT NULL,
    intent TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS agent_traces (
    trace_id TEXT PRIMARY KEY,
    task_id TEXT REFERENCES agent_tasks(task_id),
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS eval_cases (
    case_id TEXT PRIMARY KEY,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS bad_cases (
    case_id TEXT PRIMARY KEY,
    run_id TEXT,
    error_type TEXT,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

# SQLite 版的列顺序（迁移时按这个顺序写 INSERT）
TABLES: dict[str, list[str]] = {
    "users": ["user_id", "name"],
    "products": ["product_id", "name", "category", "price"],
    "orders": ["order_id", "user_id", "status", "created_at", "delivered_at", "total"],
    "order_items": ["order_id", "product_id", "quantity", "price"],
    "logistics": ["order_id", "carrier", "tracking_no", "status", "updated_at", "location"],
    "after_sale_requests": ["request_id", "order_id", "type", "reason", "status", "created_at"],
    "refunds": ["refund_id", "request_id", "order_id", "amount", "status", "created_at"],
    "tickets": ["ticket_id", "user_id", "order_id", "reason", "recommended_action",
                "status", "created_at"],
}


def dsn() -> str:
    return (os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_DSN")
            or "postgresql://hermes@127.0.0.1:55432/business")


def _normalize(value: Any) -> Any:
    """Decimal -> float, date/datetime -> SQLite 风格的字符串。"""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, date):
        return value.isoformat()
    return value


class PgStore:
    """与 ``Store`` 同接口的 PostgreSQL 实现。"""

    def __init__(self, conn: Any = None, dsn_str: str | None = None,
                 init: bool = True):
        if conn is not None:
            self._conn = conn
        else:
            try:
                import psycopg                       # 惰性导入
            except ImportError as exc:               # pragma: no cover
                raise RuntimeError(
                    "PostgreSQL 支持需要 psycopg：pip install -r requirements-db.txt"
                ) from exc
            self._conn = psycopg.connect(dsn_str or dsn(), autocommit=True)
        if init:
            self.init_schema()

    # ---- schema / helpers ---------------------------------------------
    def init_schema(self) -> None:
        with self._conn.cursor() as cur:
            cur.execute(DDL)

    def close(self) -> None:
        self._conn.close()

    def counts(self) -> dict[str, int]:
        """各表行数（演示/校验用）。"""
        return {table: self._scalar(f"SELECT COUNT(*) FROM {table}")
                for table in TABLES}

    def _scalar(self, sql: str, params: tuple = ()) -> Any:
        with self._conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            return _normalize(row[0]) if row else None

    def _query(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._conn.cursor() as cur:
            cur.execute(sql, params)
            columns = [desc[0] for desc in cur.description]
            return [{col: _normalize(val) for col, val in zip(columns, row)}
                    for row in cur.fetchall()]

    def _insert(self, sql: str, params: tuple) -> None:
        with self._conn.cursor() as cur:
            cur.execute(sql, params)

    # ---- queries（与 Store 同签名）------------------------------------
    def find_orders(self, user_id: str, order_id: str | None = None) -> list[dict]:
        if order_id:
            return self._query(
                "SELECT * FROM orders WHERE user_id = %s AND order_id = %s",
                (user_id, order_id))
        return self._query(
            "SELECT * FROM orders WHERE user_id = %s ORDER BY created_at DESC",
            (user_id,))

    def get_order(self, order_id: str) -> dict | None:
        rows = self._query("SELECT * FROM orders WHERE order_id = %s", (order_id,))
        return rows[0] if rows else None

    def get_order_items(self, order_id: str) -> list[dict]:
        return self._query(
            "SELECT oi.*, p.name AS product_name, p.category FROM order_items oi "
            "JOIN products p ON p.product_id = oi.product_id WHERE oi.order_id = %s",
            (order_id,))

    def get_logistics(self, order_id: str) -> dict | None:
        rows = self._query("SELECT * FROM logistics WHERE order_id = %s", (order_id,))
        return rows[0] if rows else None

    def get_after_sale(self, order_id: str) -> list[dict]:
        return self._query(
            "SELECT * FROM after_sale_requests WHERE order_id = %s", (order_id,))

    def get_refunds(self, order_id: str) -> list[dict]:
        return self._query("SELECT * FROM refunds WHERE order_id = %s", (order_id,))

    # ---- mutations（ID 规则与 Store 一致）-----------------------------
    def create_return_request(self, order_id: str, reason: str) -> dict:
        request_id = (f"R{demo_today().strftime('%Y%m%d')}"
                      f"{len(self.get_after_sale(order_id)) + 1:03d}")
        self._insert(
            "INSERT INTO after_sale_requests VALUES (%s, %s, %s, %s, %s, %s)",
            (request_id, order_id, "退货", reason, "created", demo_today().isoformat()))
        return {"request_id": request_id, "order_id": order_id, "type": "退货",
                "reason": reason, "status": "created"}

    def create_refund_request(self, order_id: str, amount: float) -> dict:
        refund_id = (f"RF{demo_today().strftime('%Y%m%d')}"
                     f"{len(self.get_refunds(order_id)) + 1:03d}")
        self._insert(
            "INSERT INTO refunds VALUES (%s, %s, %s, %s, %s, %s)",
            (refund_id, "R-PENDING", order_id, amount, "created",
             demo_today().isoformat()))
        return {"refund_id": refund_id, "order_id": order_id, "amount": amount,
                "status": "created"}

    def create_ticket(self, user_id: str, order_id: str | None, reason: str,
                      recommended_action: str | None) -> dict:
        ticket_id = f"T{demo_today().strftime('%Y%m%d')}0001"
        if self._scalar("SELECT COUNT(*) FROM tickets WHERE ticket_id = %s", (ticket_id,)):
            total = self._scalar("SELECT COUNT(*) FROM tickets") or 0
            ticket_id = f"T{demo_today().strftime('%Y%m%d')}{total + 1:04d}"
        self._insert(
            "INSERT INTO tickets VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (ticket_id, user_id, order_id, reason, recommended_action, "open",
             demo_today().isoformat()))
        return {"ticket_id": ticket_id, "user_id": user_id, "order_id": order_id,
                "reason": reason, "recommended_action": recommended_action,
                "status": "open"}
