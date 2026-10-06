"""API 服务层：数据库不可用时的降级与健康检查语义（不需要真起服务）。

同时是"502/500 排查"的回归测试：数据库不可用时
  * /health 仍 200，但 checks.postgres 为 false（秒回）
  * /api/chat 快速抛 ConnectionError（由路由映射成 503），而不是进 Agent 循环干等
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi", reason="需要可选依赖 fastapi")

from app.api.server import AgentService  # noqa: E402


class _StubStore:
    def __init__(self, ok: bool):
        self.ok = ok

    def ping(self, fast: bool = True) -> bool:
        return self.ok


def test_db_ready_reflects_ping():
    service = AgentService()
    service._store = _StubStore(True)
    assert service._db_ready() is True
    service._store = _StubStore(False)
    assert service._db_ready() is False


def test_chat_short_circuits_when_db_unavailable():
    """数据库不可用时必须快速失败，不能进 Agent 循环（否则被拖到客户端超时）。"""
    service = AgentService()
    service._store = _StubStore(False)
    service._minimal = object()          # 假装已初始化，跳过 _ensure 的真实建连

    with pytest.raises(ConnectionError):
        service.chat("U10003", "查订单O10003", "s1")
    assert service._db_ok is False or service._db_ok is None


def test_checks_are_none_without_any_backend_configured(monkeypatch):
    for name in ("DATABASE_URL", "POSTGRES_DSN", "REDIS_URL"):
        monkeypatch.delenv(name, raising=False)
    assert AgentService().checks() == {"postgres": None, "redis": None}


def test_checks_report_false_for_unreachable_database(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://x@127.0.0.1:1/x")
    monkeypatch.delenv("REDIS_URL", raising=False)
    result = AgentService().checks()
    assert result["postgres"] is False, "连不上必须报 false 而不是抛异常/卡住"
    assert result["redis"] is None
