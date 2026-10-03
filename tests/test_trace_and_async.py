"""Trace 视图（Step 13）与并发执行（Step 19）测试。"""
from __future__ import annotations

import asyncio
import time
from dataclasses import replace

from app.agent.async_tools import gather, gather_async, run_sequential
from app.rag.policy_rag import PolicyRAG
from app.store import reset_store
from app.tools.business import BusinessTools, build_registry
from app.tools.registry import ToolExecutor, ToolRegistry
from app.trace.tracer import Tracer
from app.trace.view import render_tree


# ---- Step 13: trace 树 -------------------------------------------------
def test_render_tree_lists_every_span():
    tracer = Tracer()
    tracer.record("user_input", input="耳机坏了")
    tracer.record("permission", input={"tool": "query_order"},
                  output={"mode": "auto", "reason": "LOW"})
    tracer.record("tool", tool="query_order", arguments={"user_id": "U10001"},
                  latency_ms=1.5, retry_count=0)
    tracer.record("final", output="已受理")

    tree = render_tree(tracer.to_dict())
    assert tracer.trace_id in tree
    lines = tree.splitlines()
    assert len(lines) == 5                                    # 头 + 4 个 span
    assert lines[1].startswith("├──") and lines[-1].startswith("└──")
    assert "query_order" in tree and "mode=auto" in tree


# ---- Step 19: 并发 -----------------------------------------------------
def _slow_executor(delay: float) -> ToolExecutor:
    tools = BusinessTools(reset_store(), policy_search=PolicyRAG().retrieve)
    base = build_registry(tools)
    slow = ToolRegistry()
    for spec in base.specs():
        original = spec.handler

        def handler(*_args, _original=original, **_kwargs):
            time.sleep(delay)
            return _original(**_kwargs)

        slow.register(replace(spec, handler=handler))
    return ToolExecutor(slow, max_retries=0, timeout_s=10.0)


CALLS = [("query_order", {"user_id": "U10001"}),
         ("query_logistics", {"order_id": "O202609004"}),
         ("search_after_sales_policy", {"query": "耳机能退吗"})]


def test_gather_preserves_order_and_results_match_sequential():
    executor = _slow_executor(0.0)
    sequential = run_sequential(executor, CALLS)
    concurrent = gather(executor, CALLS)
    assert [r.tool for r in concurrent] == [name for name, _ in CALLS]
    assert [r.data for r in concurrent] == [r.data for r in sequential]


def test_gather_is_actually_concurrent():
    delay = 0.2
    executor = _slow_executor(delay)
    start = time.perf_counter()
    run_sequential(executor, CALLS)
    sequential_ms = (time.perf_counter() - start) * 1000
    start = time.perf_counter()
    gather(executor, CALLS)
    concurrent_ms = (time.perf_counter() - start) * 1000
    # 串行 ≈ 3x,并发 ≈ 1x;给足余量避免抖动
    assert sequential_ms > delay * 1000 * 2
    assert concurrent_ms < delay * 1000 * 2


def test_gather_async_runs_calls():
    executor = _slow_executor(0.0)
    results = asyncio.run(gather_async(executor, CALLS))
    assert all(r.ok for r in results)
    assert [r.tool for r in results] == [name for name, _ in CALLS]


def test_empty_and_single_call():
    executor = _slow_executor(0.0)
    assert gather(executor, []) == []
    assert len(gather(executor, [CALLS[0]])) == 1
