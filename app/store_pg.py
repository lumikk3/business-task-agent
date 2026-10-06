"""PostgreSQL 数据层（Step 18）。

与 ``app/store.py::Store`` **同方法签名、同返回值形状、同 ID 规则**，因此
``BusinessTools`` / 工具层 / Agent 完全不用改，只换一个 store 实例。

为什么返回值和 SQLite 版长得一样：SQLite 把日期存成文本、金额存成 REAL，
上层（brain / tools）直接当字符串和 float 用。psycopg 会返回 ``date`` /
``Decimal``，所以这里统一归一化成 ISO 字符串和 float，避免上层出现
「Decimal 不能 JSON 序列化」「date 不能做字符串比较」这类问题。

连接韧性（本机实测过 PostgreSQL 重启场景）：
  1. **执行前预检**：连接已断开就先重连——这一步不会重复执行任何语句；
  2. **重连带退避重试**：数据库重启有个几秒窗口，只连一次会撞上
     ``Connection refused``，重启后的第一个请求就 500；
  3. **执行中掉线**：重连成功后把这次操作重试一次；
  4. 只对**连接类**异常做上述处理，SQL/约束错误原样抛出，不被掩盖。

注意：第 3 条对写操作理论上存在「已提交但响应丢失」时重复写入的可能。
生产上应给写操作带幂等键（request id）并做去重；本 Demo 里写操作是
「创建退货单/工单」，重复一条可人工清理，优先保证可用性。

依赖惰性导入：没装 ``psycopg`` 时 import 本模块不报错，只有真正
``PgStore()`` 时才提示安装。本机验证方式见 DEPLOY.md。
"""
from __future__ import annotations

import os
import time
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.store import demo_today

# 数据库短暂不可用时的自动恢复预算（可用环境变量覆盖）。
# 默认值是「面向交互请求」的：够覆盖 PostgreSQL 重启的几秒窗口，又不会把请求拖到几十秒。
CONNECT_RETRIES = int(os.environ.get("PG_CONNECT_RETRIES", "3"))
CONNECT_BACKOFF = float(os.environ.get("PG_CONNECT_BACKOFF", "0.2"))
CONNECT_TIMEOUT = float(os.environ.get("PG_CONNECT_TIMEOUT", "2"))
# 请求准入探测的重试次数：比健康检查多一次，用来扛住数据库重启的窗口
GATE_RETRIES = int(os.environ.get("PG_GATE_RETRIES", "2"))
# 健康检查专用：只探一次、超时更短，数据库挂了也要秒回
PING_TIMEOUT = float(os.environ.get("PG_PING_TIMEOUT", "1"))
# TCP keepalive：数据库被强杀/网络断开时，让操作系统尽快发现这个连接已经死了，
# 而不是等到下一次查询才发现（默认要等十几秒）。
KEEPALIVE_IDLE = int(os.environ.get("PG_KEEPALIVE_IDLE", "2"))

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

_PSYCOPG: Any = None


def _psycopg():
    """惰性拿到 psycopg 模块（没装时给出可操作的报错）。"""
    global _PSYCOPG
    if _PSYCOPG is None:
        try:
            import psycopg
        except ImportError as exc:                    # pragma: no cover
            raise RuntimeError(
                "PostgreSQL 支持需要 psycopg：pip install -r requirements-db.txt"
            ) from exc
        _PSYCOPG = psycopg
    return _PSYCOPG


def connection_errors() -> tuple[type[BaseException], ...]:
    """只把「连接类」错误当作可重试；SQL/约束错误不算。"""
    psycopg = _psycopg()
    return (psycopg.OperationalError, psycopg.InterfaceError, ConnectionError)


# 主机名都解析不出来（容器被停掉/DNS 没了）时，重试毫无意义——直接快速失败。
_RESOLVE_MARKERS = ("resolve", "name or service not known", "getaddrinfo",
                    "nodename nor servname", "temporary failure in name")


def is_name_resolution_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in _RESOLVE_MARKERS)


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
    """与 ``Store`` 同接口的 PostgreSQL 实现（自带断线重连）。"""

    def __init__(self, conn: Any = None, dsn_str: str | None = None,
                 init: bool = True):
        self._dsn_str = dsn_str or dsn()
        # 外部注入的连接由调用方负责生命周期（迁移脚本会这么用）,不自动重连
        self._owns_conn = conn is None
        # 构造阶段快速失败：数据库正好不可用时不要卡满重试预算,
        # 上层（API 请求）会拿到 ConnectionError 并快速返回 503,下一次请求再重试构造。
        self._conn = (conn if conn is not None
                      else self._connect(attempts=1, timeout=PING_TIMEOUT))
        if init:
            self.init_schema()

    # ---- 连接管理 ------------------------------------------------------
    def _connect(self, attempts: int | None = None, timeout: float | None = None):
        psycopg = _psycopg()
        attempts = attempts or CONNECT_RETRIES
        timeout = CONNECT_TIMEOUT if timeout is None else timeout
        last: BaseException | None = None
        for attempt in range(1, attempts + 1):
            try:
                return psycopg.connect(
                    self._dsn_str, autocommit=True, connect_timeout=timeout,
                    keepalives=1, keepalives_idle=KEEPALIVE_IDLE,
                    keepalives_interval=1, keepalives_count=2,
                )
            except connection_errors() as exc:
                last = exc
                if is_name_resolution_error(exc):
                    break                    # 主机名都解析不了 -> 服务真的没了,别重试
                if attempt < attempts:
                    time.sleep(CONNECT_BACKOFF * attempt)   # 线性退避,等数据库起来
        assert last is not None
        # 统一成 ConnectionError：上层（工具 executor / API）据此判定"后端挂了"，
        # 而不是把它当成一次普通的工具调用失败。
        raise ConnectionError(
            f"无法连接 PostgreSQL（已尝试 {attempts} 次）: {last}") from last

    def is_alive(self) -> bool:
        return self._conn is not None and not getattr(self._conn, "closed", True)

    def _reconnect(self, attempts: int | None = None,
                   timeout: float | None = None) -> bool:
        """重建连接；不是自己持有的连接则不动。失败返回 False。"""
        if not self._owns_conn:
            return False
        try:
            self._conn = self._connect(attempts=attempts, timeout=timeout)
            return True
        except connection_errors():
            return False

    def ping(self, fast: bool = True) -> bool:
        """健康检查用：能查通返回 True，否则 False（不抛异常）。

        ``fast=True``（默认）**新建一条短连接**来探，而不是复用缓存连接。原因：
        socket 假死时 ``closed`` 仍是 False，拿缓存连接去查询会一直卡到
        TCP keepalive 才发现（实测 7.5s），健康检查就失去意义了。
        ``fast=False`` 用完整的重试预算，适合"扛住数据库重启"的场景。
        """
        if not self._owns_conn:                       # 外部连接:不新建,走普通路径
            try:
                self._run("SELECT 1", (), fetch="one")
                return True
            except Exception:                         # noqa: BLE001
                return False
        if fast:
            return self._probe(attempts=1, timeout=PING_TIMEOUT)
        return self._probe(attempts=CONNECT_RETRIES, timeout=CONNECT_TIMEOUT)

    def ping_resilient(self) -> bool:
        """**请求准入**用：允许退避重试，能扛住数据库重启那几秒窗口。

        与 ``ping(fast=True)`` 的区别：健康检查要"秒回真相"，请求准入要
        "尽量别因为一次重启就把用户请求拒掉"。实测 PG 重启窗口约 1~3s，
        2 次尝试足够覆盖。
        """
        if not self._owns_conn:
            return self.ping(fast=False)
        return self._probe(attempts=GATE_RETRIES, timeout=CONNECT_TIMEOUT)

    def _probe(self, attempts: int, timeout: float) -> bool:
        try:
            conn = self._connect(attempts=attempts, timeout=timeout)
        except Exception:                                 # noqa: BLE001
            return False
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
            return True
        except Exception:                                 # noqa: BLE001
            return False
        finally:
            try:
                conn.close()
            except Exception:                             # noqa: BLE001
                pass

    def backend_pid(self) -> int | None:
        """当前连接的 PostgreSQL 后端 pid（测试里用来主动杀掉它）。"""
        return self._run("SELECT pg_backend_pid()", (), fetch="one")

    # ---- 执行 ----------------------------------------------------------
    def _run(self, sql: str, params: tuple = (), fetch: str | None = "all",
             attempts: int | None = None, timeout: float | None = None) -> Any:
        """所有语句的唯一入口。

        预检：连接已断开 -> 先重连（这一步不执行任何语句，写操作不会被重复执行）。
        若重连也失败，立刻抛 ``ConnectionError``（快速失败，不再拿死连接去试，
        否则一次不可用要等两轮重连预算）。执行中掉线则重连后重试一次。
        """
        if not self.is_alive() and not self._reconnect(attempts, timeout):
            raise ConnectionError(
                f"PostgreSQL 不可用（重连失败）: {self._dsn_str}")
        try:
            return self._run_once(sql, params, fetch)
        except connection_errors():
            if not self._reconnect(attempts, timeout):
                raise
            return self._run_once(sql, params, fetch)

    def _run_once(self, sql: str, params: tuple, fetch: str | None) -> Any:
        with self._conn.cursor() as cur:
            cur.execute(sql, params)
            if fetch == "one":
                row = cur.fetchone()
                return _normalize(row[0]) if row else None
            if fetch == "all":
                columns = [desc[0] for desc in cur.description]
                return [{col: _normalize(val) for col, val in zip(columns, row)}
                        for row in cur.fetchall()]
            return None

    # ---- schema / helpers ---------------------------------------------
    def init_schema(self) -> None:
        self._run(DDL, (), fetch=None)

    def close(self) -> None:
        if self._owns_conn and self._conn is not None:
            self._conn.close()

    def counts(self) -> dict[str, int]:
        """各表行数（演示/校验用）。"""
        return {table: self._run(f"SELECT COUNT(*) FROM {table}", (), fetch="one") or 0
                for table in TABLES}

    # ---- queries（与 Store 同签名）------------------------------------
    def find_orders(self, user_id: str, order_id: str | None = None) -> list[dict]:
        if order_id:
            return self._run(
                "SELECT * FROM orders WHERE user_id = %s AND order_id = %s",
                (user_id, order_id))
        return self._run(
            "SELECT * FROM orders WHERE user_id = %s ORDER BY created_at DESC",
            (user_id,))

    def get_order(self, order_id: str) -> dict | None:
        rows = self._run("SELECT * FROM orders WHERE order_id = %s", (order_id,))
        return rows[0] if rows else None

    def get_order_items(self, order_id: str) -> list[dict]:
        return self._run(
            "SELECT oi.*, p.name AS product_name, p.category FROM order_items oi "
            "JOIN products p ON p.product_id = oi.product_id WHERE oi.order_id = %s",
            (order_id,))

    def get_logistics(self, order_id: str) -> dict | None:
        rows = self._run("SELECT * FROM logistics WHERE order_id = %s", (order_id,))
        return rows[0] if rows else None

    def get_after_sale(self, order_id: str) -> list[dict]:
        return self._run(
            "SELECT * FROM after_sale_requests WHERE order_id = %s", (order_id,))

    def get_refunds(self, order_id: str) -> list[dict]:
        return self._run("SELECT * FROM refunds WHERE order_id = %s", (order_id,))

    # ---- mutations（ID 规则与 Store 一致）-----------------------------
    def create_return_request(self, order_id: str, reason: str) -> dict:
        request_id = (f"R{demo_today().strftime('%Y%m%d')}"
                      f"{len(self.get_after_sale(order_id)) + 1:03d}")
        self._run(
            "INSERT INTO after_sale_requests VALUES (%s, %s, %s, %s, %s, %s)",
            (request_id, order_id, "退货", reason, "created", demo_today().isoformat()),
            fetch=None)
        return {"request_id": request_id, "order_id": order_id, "type": "退货",
                "reason": reason, "status": "created"}

    def create_refund_request(self, order_id: str, amount: float) -> dict:
        refund_id = (f"RF{demo_today().strftime('%Y%m%d')}"
                     f"{len(self.get_refunds(order_id)) + 1:03d}")
        self._run(
            "INSERT INTO refunds VALUES (%s, %s, %s, %s, %s, %s)",
            (refund_id, "R-PENDING", order_id, amount, "created",
             demo_today().isoformat()),
            fetch=None)
        return {"refund_id": refund_id, "order_id": order_id, "amount": amount,
                "status": "created"}

    def create_ticket(self, user_id: str, order_id: str | None, reason: str,
                      recommended_action: str | None) -> dict:
        ticket_id = f"T{demo_today().strftime('%Y%m%d')}0001"
        if self._run("SELECT COUNT(*) FROM tickets WHERE ticket_id = %s",
                     (ticket_id,), fetch="one"):
            total = self._run("SELECT COUNT(*) FROM tickets", (), fetch="one") or 0
            ticket_id = f"T{demo_today().strftime('%Y%m%d')}{total + 1:04d}"
        self._run(
            "INSERT INTO tickets VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (ticket_id, user_id, order_id, reason, recommended_action, "open",
             demo_today().isoformat()),
            fetch=None)
        return {"ticket_id": ticket_id, "user_id": user_id, "order_id": order_id,
                "reason": reason, "recommended_action": recommended_action,
                "status": "open"}
