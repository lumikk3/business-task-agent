#!/usr/bin/env python3
"""Step 19 演示：把独立的工具调用并发执行。

三个互不依赖的查询（订单 / 物流 / 政策），给每个工具注入 300ms 模拟延迟：

    串行: Order -> Logistics -> Policy        ≈ 3 x 300ms
    并发: Order / Logistics / Policy 同时跑    ≈ 1 x 300ms

    python scripts/run_async_demo.py
"""
from __future__ import annotations

import asyncio
import sys
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.agent.async_tools import gather, gather_async, run_sequential  # noqa: E402
from app.memory.context import MemoryStore  # noqa: E402
from app.rag.policy_rag import PolicyRAG  # noqa: E402
from app.store import reset_store  # noqa: E402
from app.tools.business import BusinessTools, build_registry  # noqa: E402
from app.tools.registry import ToolExecutor, ToolRegistry  # noqa: E402

DELAY = 0.3
CALLS = [
    ("query_order", {"user_id": "U10001"}),
    ("query_logistics", {"order_id": "O202609004"}),
    ("search_after_sales_policy", {"query": "耳机能退吗"}),
]


def _slow_registry():
    """给每个工具包一层固定延迟,用来放大串行/并发的差别。"""
    tools = BusinessTools(reset_store(), policy_search=PolicyRAG().retrieve)
    registry = build_registry(tools)
    slow = ToolRegistry()
    for spec in registry.specs():
        original = spec.handler

        def handler(*_args, _original=original, **_kwargs):
            time.sleep(DELAY)
            return _original(**_kwargs)

        slow.register(replace(spec, handler=handler))
    return slow


def _timed(fn):
    start = time.perf_counter()
    results = fn()
    return (time.perf_counter() - start) * 1000, results


def main() -> None:
    executor = ToolExecutor(_slow_registry(), max_retries=0, timeout_s=10.0)
    MemoryStore()  # 保持与其他 demo 一致的初始化顺序

    seq_ms, seq = _timed(lambda: run_sequential(executor, CALLS))
    par_ms, par = _timed(lambda: gather(executor, CALLS))
    aio_ms, aio = _timed(lambda: asyncio.run(gather_async(executor, CALLS)))

    print(f"每个工具注入 {DELAY*1000:.0f}ms 延迟,共 {len(CALLS)} 个独立调用\n")
    print(f"串行 (Order -> Logistics -> Policy) : {seq_ms:7.1f} ms")
    print(f"并发 线程池 gather                  : {par_ms:7.1f} ms")
    print(f"并发 asyncio.gather                 : {aio_ms:7.1f} ms")
    print(f"\n加速比: 串行/并发 = {seq_ms / par_ms:.2f}x")
    assert all(result.ok for result in seq + par + aio), "并发不应改变结果"
    print("结果一致性: 串行与并发结果全部 ok ✓")


if __name__ == "__main__":
    main()
