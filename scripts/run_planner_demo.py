#!/usr/bin/env python3
"""Step 7 演示：Planner 负责「做什么」，Runtime 负责「怎么可靠地执行」。

复杂任务： "我的耳机坏了,而且订单好像也找不到了,我想退货。"

Planner 先生成步骤，然后 Runtime 真的把这些步骤执行掉（带权限/重试/Trace）。
用确定性 RuleBasedBrain，不需要 LLM / API Key。

    python scripts/run_planner_demo.py
"""
from __future__ import annotations

import json
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

QUESTION = "我的耳机坏了,而且订单好像也找不到了,我想退货。"
USER_ID = "U10003"


def main() -> None:
    if not Path(DEFAULT_DB_PATH).exists():
        generate(DEFAULT_DB_PATH)
    store = open_scratch_store(str(DEFAULT_DB_PATH))
    tools = BusinessTools(store, policy_search=PolicyRAG().retrieve)
    registry = build_registry(tools)
    executor = ToolExecutor(registry, max_retries=2, timeout_s=3.0, sleep=lambda _s: None)

    with tempfile.TemporaryDirectory() as tmp:
        runtime = AgentRuntime(RuleBasedBrain(), registry, executor, MemoryStore(),
                               trace_dir=tmp)
        result = runtime.run(QUESTION, user_id=USER_ID, session_id="planner-demo")

        print(f"用户   : {QUESTION}")
        print(f"识别意图: {result.intent}")
        print("\n[Planner] 生成的执行计划:")
        for i, step in enumerate(result.plan, start=1):
            print(f"  {i}. {step}")

        print("\n[Runtime] 实际执行的工具链:")
        for i, call in enumerate(result.tool_calls, start=1):
            flag = "ok" if call.get("ok") else "FAIL"
            print(f"  {i}. {call['tool']}  [{flag}]")

        trace_path = Path(tmp) / "agent_traces.jsonl"
        nodes = []
        if trace_path.exists():
            trace = json.loads(trace_path.read_text(encoding="utf-8").strip())
            nodes = [span["node"] for span in trace["spans"]]
        print(f"\n[Trace] 完整执行节点: {' -> '.join(nodes)}")
        print(f"\n状态   : {result.status}   风险: {result.risk_level}")
        print(f"回答   : {result.final_response}")
        print("\n一句话: Planner 决定「做什么」(上面的计划), "
              "Runtime 负责「怎么可靠地执行」(权限校验/重试/人工接管/Trace)。")


if __name__ == "__main__":
    main()
