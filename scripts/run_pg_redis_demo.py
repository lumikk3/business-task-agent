#!/usr/bin/env python3
"""Step 18 验证：让 Agent 真的跑在 PostgreSQL + Redis 上。

前置（见 DEPLOY.md）：
    scripts/dev_services.sh up
    eval "$(scripts/dev_services.sh env)"
    python scripts/migrate_to_pg.py

本脚本会：
  1. 打印后端选择（postgres / redis）
  2. 用 PgStore 起 Agent，跑一个退货任务，验证新退货单**写进了 PostgreSQL**
  3. 用 RedisSessionMemory 记录会话，并直接读 Redis 里的原始值验证落盘
  4. 对比 SQLite 与 PostgreSQL 的同一查询结果是否一致
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.agent.brain import RuleBasedBrain  # noqa: E402
from app.agent.runtime import AgentRuntime  # noqa: E402
from app.backend import (backend_names, database_url, open_business_store,  # noqa: E402
                         open_session_memory, redis_url)
from app.memory.context import MemoryStore  # noqa: E402
from app.rag.policy_rag import PolicyRAG  # noqa: E402
from app.store import open_scratch_store  # noqa: E402
from app.tools.business import BusinessTools, build_registry  # noqa: E402
from app.tools.registry import ToolExecutor  # noqa: E402

QUESTION = "耳机坏了,我要退货。"
USER_ID = "U10003"


def main() -> None:
    names = backend_names()
    print(f"后端选择: business_store={names['business_store']}  "
          f"session_memory={names['session_memory']}")
    print(f"  DATABASE_URL={database_url()}")
    print(f"  REDIS_URL   ={redis_url()}\n")

    if names["business_store"] != "postgres":
        print("未设置 DATABASE_URL —— 先跑 scripts/dev_services.sh up && "
              'eval "$(scripts/dev_services.sh env)"')
        raise SystemExit(2)

    # ---- 1) PostgreSQL：把 Agent 跑起来 ---------------------------------
    store = open_business_store()
    print(f"业务数据层: {type(store).__module__}.{type(store).__name__}")
    before = store.counts()
    print("PG 行数(前): " + ", ".join(f"{k}={v}" for k, v in list(before.items())[:4]))

    tools = BusinessTools(store, policy_search=PolicyRAG().retrieve)
    registry = build_registry(tools)
    executor = ToolExecutor(registry, max_retries=1, timeout_s=5.0, sleep=lambda _s: None)
    runtime = AgentRuntime(RuleBasedBrain(), registry, executor, MemoryStore())

    result = runtime.run(QUESTION, user_id=USER_ID, session_id="pg-demo")
    chain = " -> ".join(c["tool"] for c in result.tool_calls)
    print(f"\nAgent 工具链 (跑在 PG 上): {chain}")
    print(f"状态: {result.status}")
    print(f"回答: {result.final_response[:80]}")

    after = store.counts()
    created = after["after_sale_requests"] - before["after_sale_requests"]
    print(f"\nPostgreSQL after_sale_requests: {before['after_sale_requests']} "
          f"-> {after['after_sale_requests']}  (新增 {created})")
    rows = store.get_after_sale("O10003")
    assert created == 1, "退货申请没有写进 PostgreSQL"
    print(f"从 PG 读回的退货单: {json.dumps(rows[-1], ensure_ascii=False)}")

    # ---- 2) 与 SQLite 结果对比 ------------------------------------------
    sqlite_store = open_scratch_store()
    pg_order = store.find_orders(USER_ID)[0]
    lite_order = sqlite_store.find_orders(USER_ID)[0]
    print(f"\n同一查询对比:")
    print(f"  SQLite     : {json.dumps(lite_order, ensure_ascii=False)[:110]}")
    print(f"  PostgreSQL : {json.dumps(pg_order, ensure_ascii=False)[:110]}")
    same_keys = set(pg_order) == set(lite_order)
    same_vals = {k: pg_order[k] for k in ("order_id", "status", "total", "delivered_at")} == \
                {k: lite_order[k] for k in ("order_id", "status", "total", "delivered_at")}
    print(f"  字段一致: {same_keys}   关键值一致: {same_vals}")
    assert same_keys and same_vals, "PgStore 与 Store 返回形状/取值不一致"

    # ---- 3) Redis：会话记忆 ---------------------------------------------
    if not redis_url():
        print("\n未设置 REDIS_URL，跳过 Redis 部分")
        return
    memory = open_session_memory()
    print(f"\n会话记忆层: {type(memory).__module__}.{type(memory).__name__}")
    # 用真实的工具结果喂给记忆（和 Agent 里的调用形状一致）
    memory.observe("redis-demo", "query_order", tools.query_order(USER_ID))
    memory.observe("redis-demo", "create_return_request", {"return_request": rows[-1]})
    memory.note("redis-demo", "user_id", USER_ID)
    memory.remember_turn("redis-demo", QUESTION, result.final_response, "RETURN_REQUEST")
    print("context_block:")
    for line in memory.context_block("redis-demo").splitlines():
        print(f"  {line}")
    assert "O10003" in memory.context_block("redis-demo"), "记忆里没有当前订单"

    import redis as redis_lib
    client = redis_lib.Redis.from_url(redis_url(), decode_responses=True)
    raw = client.get("agent:session:redis-demo")
    print(f"\nRedis 原始 key agent:session:redis-demo "
          f"(ttl={client.ttl('agent:session:redis-demo')}s):")
    print(f"  {str(raw)[:220]}...")
    assert raw and "O10003" in raw, "Redis 里没有会话数据"

    # 换个进程视角：新建一个 memory 实例，从 Redis 读回同样的上下文
    fresh = open_session_memory()
    fresh._sessions.clear()                       # 丢掉进程内缓存，强制从 Redis 读
    assert "O10003" in fresh.context_block("redis-demo"), "新实例读不回会话"
    print("  新实例从 Redis 读回同一上下文 ✓")
    print("\n✓ Agent 完整跑在 PostgreSQL + Redis 上")


if __name__ == "__main__":
    main()
