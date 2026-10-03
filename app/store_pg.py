"""PostgreSQL 数据层（Step 18）。

⚠️ 本机没有 PostgreSQL 服务,这个模块**未在本机运行验证过** —— 它是 V2 的落地代码与 DDL,
语义与 ``app/store.py`` 的 SQLite 版一一对应（同一套表 + 同一批查询/写方法）。

设计上与 SQLite 版的关系：``PgStore`` 暴露与 ``Store`` 相同的方法签名,因此
``BusinessTools`` / 工具层 / Agent 完全不用改。

依赖是惰性导入的：没装 ``psycopg`` 时 import 本模块不会报错。
"""
from __future__ import annotations

import os
from typing import Any

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


def dsn() -> str:
    return os.environ.get("DATABASE_URL") or os.environ.get(
        "POSTGRES_DSN", "postgresql://hermes:hermes@localhost:5432/business")


def init_schema(conn: Any) -> None:
    with conn.cursor() as cur:
        cur.execute(DDL)
    conn.commit()


class PgStore:
    """与 ``app.store.Store`` 同接口的 PostgreSQL 实现。

    ``Store`` 的查询是同步 sqlite3；这里用 psycopg 的同步连接,同样给 Agent 用。
    """

    def __init__(self, conn: Any = None, dsn_str: str | None = None):
        if conn is not None:
            self._conn = conn
        else:
            try:
                import psycopg                     # 惰性导入：没装也不影响 import 本模块
            except ImportError as exc:             # pragma: no cover
                raise RuntimeError(
                    "PostgreSQL 支持需要 psycopg：pip install -r requirements-db.txt"
                ) from exc
            self._conn = psycopg.connect(dsn_str or dsn())

    # ---- queries（与 Store 同签名）------------------------------------
    def find_orders(self, user_id: str, order_id: str | None = None) -> list[dict]:
        sql = "SELECT * FROM orders WHERE user_id = %s"
        params: list[Any] = [user_id]
        if order_id:
            sql += " AND order_id = %s"
            params.append(order_id)
        sql += " ORDER BY created_at DESC"
        return self._rows(sql, params)

    def get_order(self, order_id: str) -> dict | None:
        rows = self._rows("SELECT * FROM orders WHERE order_id = %s", [order_id])
        return rows[0] if rows else None

    def get_order_items(self, order_id: str) -> list[dict]:
        return self._rows(
            "SELECT oi.*, p.name AS product_name, p.category FROM order_items oi "
            "JOIN products p ON p.product_id = oi.product_id WHERE oi.order_id = %s",
            [order_id])

    def get_logistics(self, order_id: str) -> dict | None:
        rows = self._rows("SELECT * FROM logistics WHERE order_id = %s", [order_id])
        return rows[0] if rows else None

    def get_after_sale(self, order_id: str) -> list[dict]:
        return self._rows("SELECT * FROM after_sale_requests WHERE order_id = %s",
                          [order_id])

    def get_refunds(self, order_id: str) -> list[dict]:
        return self._rows("SELECT * FROM refunds WHERE order_id = %s", [order_id])

    # ---- mutations -----------------------------------------------------
    def create_return_request(self, order_id: str, reason: str) -> dict:
        request_id = self._next_id("after_sale_requests", "request_id", "R")
        self._execute(
            "INSERT INTO after_sale_requests VALUES (%s, %s, %s, %s, %s, CURRENT_DATE)",
            [request_id, order_id, "退货", reason, "created"])
        return {"request_id": request_id, "order_id": order_id, "type": "退货",
                "reason": reason, "status": "created"}

    def create_refund_request(self, order_id: str, amount: float) -> dict:
        refund_id = self._next_id("refunds", "refund_id", "RF")
        self._execute(
            "INSERT INTO refunds VALUES (%s, %s, %s, %s, %s, CURRENT_DATE)",
            [refund_id, "R-PENDING", order_id, amount, "created"])
        return {"refund_id": refund_id, "order_id": order_id, "amount": amount,
                "status": "created"}

    def create_ticket(self, user_id: str, order_id: str | None, reason: str,
                      recommended_action: str | None) -> dict:
        ticket_id = self._next_id("tickets", "ticket_id", "T")
        self._execute(
            "INSERT INTO tickets VALUES (%s, %s, %s, %s, %s, %s, CURRENT_DATE)",
            [ticket_id, user_id, order_id, reason, recommended_action, "open"])
        return {"ticket_id": ticket_id, "user_id": user_id, "order_id": order_id,
                "reason": reason, "recommended_action": recommended_action,
                "status": "open"}

    # ---- helpers -------------------------------------------------------
    def _rows(self, sql: str, params: list[Any]) -> list[dict]:
        with self._conn.cursor() as cur:
            cur.execute(sql, params)
            columns = [desc[0] for desc in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]

    def _execute(self, sql: str, params: list[Any]) -> None:
        with self._conn.cursor() as cur:
            cur.execute(sql, params)
        self._conn.commit()

    def _next_id(self, table: str, column: str, prefix: str) -> str:
        rows = self._rows(f"SELECT COUNT(*) AS n FROM {table}", [])
        return f"{prefix}{100001 + int(rows[0]['n'])}"
