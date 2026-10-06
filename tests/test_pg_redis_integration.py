"""Step 18 集成测试：真实 PostgreSQL + Redis。

本机没起服务时自动 skip（CI 也能跑），起了就跑真的。

    scripts/dev_services.sh up
    eval "$(scripts/dev_services.sh env)"
    python scripts/migrate_to_pg.py
    python -m pytest -q tests/test_pg_redis_integration.py

注意：这些测试会真的往开发库写数据（新增退货申请）。迁移脚本幂等，重跑即可复原。
"""
from __future__ import annotations

import pytest

from app.backend import database_url, open_business_store, open_session_memory, redis_url


def _pg_reachable() -> bool:
    if not database_url():
        return False
    try:
        import psycopg
        with psycopg.connect(database_url(), connect_timeout=2):
            return True
    except Exception:
        return False


def _redis_reachable() -> bool:
    if not redis_url():
        return False
    try:
        import redis
        return bool(redis.Redis.from_url(redis_url(), socket_connect_timeout=2).ping())
    except Exception:
        return False


pg_only = pytest.mark.skipif(not _pg_reachable(),
                             reason="需要 PostgreSQL（scripts/dev_services.sh up）")
redis_only = pytest.mark.skipif(not _redis_reachable(),
                                reason="需要 Redis（scripts/dev_services.sh up）")


@pg_only
def test_pg_store_returns_same_shape_as_sqlite():
    from app.store import open_scratch_store

    pg = open_business_store()
    lite = open_scratch_store()
    for user_id in ("U10001", "U10002", "U10003"):
        pg_order = pg.find_orders(user_id)[0]
        lite_order = lite.find_orders(user_id)[0]
        assert set(pg_order) == set(lite_order)
        for key in ("order_id", "status", "total", "created_at", "delivered_at"):
            assert pg_order[key] == lite_order[key], f"{user_id}.{key}"

    order_id = pg.find_orders("U10003")[0]["order_id"]
    assert pg.get_order_items(order_id) == lite.get_order_items(order_id)
    assert pg.get_logistics("O10002") == lite.get_logistics("O10002")


@pg_only
def test_agent_writes_through_to_postgres():
    from app.agent.brain import RuleBasedBrain
    from app.agent.runtime import AgentRuntime
    from app.memory.context import MemoryStore
    from app.rag.policy_rag import PolicyRAG
    from app.tools.business import BusinessTools, build_registry
    from app.tools.registry import ToolExecutor

    store = open_business_store()
    before = store.counts()["after_sale_requests"]

    tools = BusinessTools(store, policy_search=PolicyRAG().retrieve)
    registry = build_registry(tools)
    executor = ToolExecutor(registry, max_retries=1, timeout_s=5.0, sleep=lambda _s: None)
    result = AgentRuntime(RuleBasedBrain(), registry, executor, MemoryStore()).run(
        "耳机坏了,我要退货。", user_id="U10003", session_id="pg-test")

    assert result.status == "completed"
    assert "create_return_request" in [c["tool"] for c in result.tool_calls]
    after = store.counts()
    assert after["after_sale_requests"] == before + 1      # 真的写进了 PG
    assert store.get_after_sale("O10003"), "PG 里读不回刚创建的退货单"


@pg_only
def test_pg_store_recovers_after_admin_shutdown():
    """真实制造一次 AdminShutdown：让服务端杀掉本连接，下一次查询必须自动恢复。

    ``pg_terminate_backend`` 触发的正是 ``FATAL: terminating connection due to
    administrator command`` —— 与 PostgreSQL 重启（AdminShutdown）同一类错误。
    """
    import psycopg

    store = open_business_store()
    assert store.find_orders("U10003"), "前置条件：先确认连接可用"

    pid = store.backend_pid()
    assert pid, "拿不到 backend pid"
    with psycopg.connect(database_url(), autocommit=True) as killer:
        with killer.cursor() as cur:
            cur.execute("SELECT pg_terminate_backend(%s)", (pid,))

    # 连接已被服务端终止 -> 必须自动重连，而不是把错误抛给调用方
    assert store.find_orders("U10003"), "断线后没有自动恢复"
    assert store.ping() is True


@redis_only
def test_redis_session_memory_persists_across_instances():
    first = open_session_memory()
    first.observe("it-session", "query_order",
                  {"orders": [{"order_id": "O10003", "product": "无线耳机"}]})
    first.remember_turn("it-session", "耳机坏了", "已受理", "RETURN_REQUEST")

    assert "O10003" in first.context_block("it-session")

    # 新实例（清掉进程内缓存）必须能从 Redis 读回同样的上下文
    second = open_session_memory()
    second._sessions.clear()
    block = second.context_block("it-session")
    assert "O10003" in block and "无线耳机" in block
    assert second.history("it-session")[-1]["intent"] == "RETURN_REQUEST"
