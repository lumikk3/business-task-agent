"""PgStore 断线重连（Skill 18 / 运维韧性）单元测试。

不需要真实 PostgreSQL：用假连接精确制造三种情形
  1) 连接已经死掉（预检就该重连，且不要在死连接上跑语句）
  2) 连接看着活着但执行时报连接类错误（重连后重试一次）
  3) SQL/约束错误（不是连接问题）必须原样抛出，不能被重连逻辑吞掉
  4) 外部注入的连接不接管生命周期
"""
from __future__ import annotations

import pytest

pytest.importorskip("psycopg", reason="需要 psycopg：pip install -r requirements-db.txt")

import psycopg  # noqa: E402

from app.store_pg import PgStore  # noqa: E402


class _FakeCursor:
    def __init__(self, rows=None, error: BaseException | None = None):
        self._rows = rows or []
        self._error = error
        self.description = [("n",)] if rows is not None else None

    def execute(self, sql, params=()):
        if self._error is not None:
            raise self._error

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConn:
    """closed 可控、cursor() 可注入错误的假连接。"""

    def __init__(self, closed: bool = False, cursor_error: BaseException | None = None):
        self.closed = closed
        self.cursor_error = cursor_error
        self.cursor_calls = 0

    def cursor(self):
        self.cursor_calls += 1
        return _FakeCursor(rows=[(1,)], error=self.cursor_error)

    def close(self):
        self.closed = True


class _SeqPgStore(PgStore):
    """跳过真实连接：按顺序发放预置连接，并记录重连次数。"""

    def __init__(self, conns: list[_FakeConn]):
        self._dsn_str = "fake-dsn"
        self._owns_conn = True
        self._pending = list(conns)
        self._conn = self._pending.pop(0)
        self.reconnects = 0

    def _connect(self, attempts=None, timeout=None):
        self.reconnects += 1
        return self._pending.pop(0)


def test_reconnects_when_connection_already_dead():
    dead = _FakeConn(closed=True)
    alive = _FakeConn()
    store = _SeqPgStore([dead, alive])

    assert store.find_orders("U10003") == [{"n": 1}]
    assert store.reconnects == 1
    assert dead.cursor_calls == 0, "不应在已断开的连接上执行语句"


def test_retries_once_after_connection_error_mid_query():
    broken = _FakeConn(cursor_error=psycopg.OperationalError(
        "terminating connection due to administrator command"))
    alive = _FakeConn()
    store = _SeqPgStore([broken, alive])

    assert store.find_orders("U10003") == [{"n": 1}]
    assert store.reconnects == 1
    assert broken.cursor_calls == 1 and alive.cursor_calls == 1


def test_sql_errors_are_not_masked_by_reconnect():
    broken = _FakeConn(cursor_error=psycopg.errors.UndefinedTable('relation "nope" missing'))
    store = _SeqPgStore([broken, _FakeConn()])

    with pytest.raises(psycopg.errors.UndefinedTable):
        store.find_orders("U10003")
    assert store.reconnects == 0, "SQL 错误不该触发重连"


def test_ping_reports_false_instead_of_raising():
    store = _SeqPgStore([_FakeConn(cursor_error=psycopg.OperationalError("down")),
                         _FakeConn(cursor_error=psycopg.OperationalError("still down"))])
    assert store.ping() is False


def test_external_connection_is_not_reconnected():
    store = PgStore(conn=_FakeConn(cursor_error=psycopg.OperationalError("down")), init=False)
    assert store._owns_conn is False
    with pytest.raises(psycopg.OperationalError):
        store.find_orders("U10003")


class _UnreachablePgStore(PgStore):
    """数据库整台不可达：记录每次重连用的预算参数。"""

    def __init__(self, closed: bool = True):
        self._dsn_str = "fake-dsn"
        self._owns_conn = True
        self._conn = _FakeConn(closed=closed)
        self.calls: list[tuple] = []

    def _connect(self, attempts=None, timeout=None):
        self.calls.append((attempts, timeout))
        raise psycopg.OperationalError("connection refused")


def test_fail_fast_when_database_is_unreachable():
    """重连失败要快速失败成 ConnectionError，而不是拿死连接再试两轮。"""
    store = _UnreachablePgStore()
    with pytest.raises(ConnectionError):
        store.find_orders("U10003")
    assert len(store.calls) == 1, "应该只做一轮重连尝试"


def test_fast_ping_uses_single_short_attempt():
    from app.store_pg import PING_TIMEOUT

    store = _UnreachablePgStore()
    assert store.ping() is False
    assert store.calls == [(1, PING_TIMEOUT)], "健康检查必须只探一次、用短超时"


def test_full_ping_uses_normal_budget():
    from app.store_pg import CONNECT_RETRIES, CONNECT_TIMEOUT

    store = _UnreachablePgStore()
    assert store.ping(fast=False) is False
    assert store.calls == [(CONNECT_RETRIES, CONNECT_TIMEOUT)]


def test_resilient_ping_uses_gate_budget():
    """请求准入探测要比健康检查多留一点重试，用来扛住数据库重启窗口。"""
    from app.store_pg import CONNECT_TIMEOUT, GATE_RETRIES, PING_TIMEOUT

    store = _UnreachablePgStore()
    assert store.ping_resilient() is False
    assert store.calls == [(GATE_RETRIES, CONNECT_TIMEOUT)]
    assert GATE_RETRIES > 1 and PING_TIMEOUT < CONNECT_TIMEOUT


def test_classify_name_resolution_failure():
    from app.store_pg import is_name_resolution_error

    dns = psycopg.OperationalError(
        "connection failed: failed to resolve host 'postgres': "
        "[Errno -2] Name or service not known")
    refused = psycopg.OperationalError(
        'connection failed: connection to server at "127.0.0.1", port 5432 '
        "failed: Connection refused")
    assert is_name_resolution_error(dns) is True
    assert is_name_resolution_error(refused) is False


def _store_with_connect_error(monkeypatch, message: str):
    """把底层 connect 换成固定报错，避免真的做 DNS 解析（本机解析失败要 ~10s）。"""
    import types

    import app.store_pg as sp

    sleeps: list[float] = []
    monkeypatch.setattr(sp.time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(sp, "_psycopg", lambda: types.SimpleNamespace(
        connect=lambda *_a, **_kw: (_ for _ in ()).throw(
            psycopg.OperationalError(message)),
        OperationalError=psycopg.OperationalError,
        InterfaceError=psycopg.InterfaceError,
    ))
    store = sp.PgStore.__new__(sp.PgStore)
    store._dsn_str = "postgresql://x@postgres:5432/x"
    return sp, store, sleeps


def test_connect_does_not_retry_when_hostname_unresolvable(monkeypatch):
    """容器被停掉后主机名解析不了——重试没意义，必须快速失败而不是退避等半天。"""
    sp, store, sleeps = _store_with_connect_error(
        monkeypatch,
        "connection failed: failed to resolve host 'postgres': "
        "[Errno -2] Name or service not known")
    with pytest.raises(ConnectionError):
        store._connect(attempts=3, timeout=1)
    assert sleeps == [], "解析失败不该继续退避重试"


def test_connect_still_retries_on_connection_refused(monkeypatch):
    """数据库还在启动（端口没起来）时要退避重试——这才是重启能自愈的关键。"""
    sp, store, sleeps = _store_with_connect_error(
        monkeypatch,
        'connection to server at "postgres", port 5432 failed: Connection refused')
    with pytest.raises(ConnectionError):
        store._connect(attempts=3, timeout=1)
    assert len(sleeps) == 2, "两次失败后应各退避一次"
