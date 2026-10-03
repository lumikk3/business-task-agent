#!/usr/bin/env python3
"""Step 13 演示：记录并查看整个 Agent 生命周期（Trace）。

跑一个退货任务，把执行链路落盘，再渲染成树：

    TASK ── Intent ── Planner ── query_order ── RAG ── create_return ── Final

用确定性 RuleBasedBrain，不需要 LLM / API Key。

    python scripts/run_trace_demo.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.agent.brain import RuleBasedBrain  # noqa: E402
from app.agent.runtime import AgentRuntime  # noqa: E402
from app.data.generator import DEFAULT_DB_PATH, generate  # noqa: E402
from app.memory.context import MemoryStore  # noqa: E402
from app.rag.policy_rag import PolicyRAG  # noqa: E402
from app.store import open_scratch_store  # noqa: E402
from app.tools.business import BusinessTools, build_registry  # noqa: E402
from app.tools.registry import ToolExecutor  # noqa: E402
from app.trace.view import render_last  # noqa: E402

QUESTION = "耳机坏了,我要退货。"
USER_ID = "U10003"


def main() -> None:
    if not Path(DEFAULT_DB_PATH).exists():
        generate(DEFAULT_DB_PATH)
    store = open_scratch_store(str(DEFAULT_DB_PATH))
    tools = BusinessTools(store, policy_search=PolicyRAG().retrieve)
    registry = build_registry(tools)
    executor = ToolExecutor(registry, max_retries=2, timeout_s=3.0, sleep=lambda _s: None)

    with tempfile.TemporaryDirectory() as tmp:
        trace_file = Path(tmp) / "agent_traces.jsonl"
        runtime = AgentRuntime(RuleBasedBrain(), registry, executor, MemoryStore(),
                               trace_dir=tmp)
        result = runtime.run(QUESTION, user_id=USER_ID, session_id="trace-demo")

        print(f"用户   : {QUESTION}")
        print(f"task_id: {result.task_id}   trace: {result.trace_id}\n")
        print("Agent Trace")
        print(render_last(trace_file))
        print(f"\n状态   : {result.status}")
        print(f"回答   : {result.final_response}")


if __name__ == "__main__":
    main()
