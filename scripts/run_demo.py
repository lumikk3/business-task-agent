#!/usr/bin/env python3
"""Run the six core scenarios from DESIGN.md section 4.

Usage:
    python scripts/run_demo.py

Uses the deterministic RuleBasedBrain by default. If OPENAI_API_KEY is set
(and optionally OPENAI_BASE_URL / OPENAI_MODEL), the same runtime runs with
the LLM brain instead. Traces are written to traces/agent_traces.jsonl.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.agent.brain import RuleBasedBrain
from app.agent.llm_brain import OpenAIBrain, has_llm_credentials
from app.agent.runtime import AgentRuntime
from app.memory.context import MemoryStore
from app.rag.policy_rag import PolicyRAG
from app.store import reset_store
from app.tools.business import BusinessTools, build_registry
from app.tools.registry import ToolExecutor

TRACE_DIR = ROOT / "traces"

SCENARIOS = [
    ("场景1 查询订单", "U10001", "我的订单什么时候发货?"),
    ("场景2 查询物流", "U10002", "我的快递到哪里了?"),
    ("场景3 售后政策咨询", "U10001", "耳机用了5天还能退吗?"),
    ("场景4 申请退货", "U10001", "耳机坏了,我要退货。"),
    ("场景5 退款查询", "U10001", "商品已经退回去了,什么时候退款?"),
    ("场景6 异常情况人工接管", "U10002",
     "耳机超过售后期限了,但确实坏了,我要退货退款,金额超过1000元。"),
]


def build_runtime() -> AgentRuntime:
    store = reset_store()
    rag = PolicyRAG()
    tools = BusinessTools(store, policy_search=rag.retrieve)
    registry = build_registry(tools)
    executor = ToolExecutor(registry, max_retries=2, timeout_s=3.0,
                            sleep=lambda _s: None)
    brain = OpenAIBrain(registry) if has_llm_credentials() else RuleBasedBrain()
    print(f"[brain] {type(brain).__name__}")
    return AgentRuntime(brain, registry, executor,
                        MemoryStore(), trace_dir=TRACE_DIR)


def show(title: str, result) -> None:
    print(f"\n=== {title} ===")
    print(f"意图   : {result.intent}")
    print(f"计划   : {' -> '.join(result.plan)}")
    print(f"工具链 : {' -> '.join(c['tool'] for c in result.tool_calls) or '(无)'}")
    print(f"状态   : {result.status}   风险: {result.risk_level}   Trace: {result.trace_id}")
    print(f"回答   : {result.final_response}")


def main() -> None:
    runtime = build_runtime()
    for title, user_id, text in SCENARIOS:
        result = runtime.run(text, user_id=user_id, session_id=f"demo-{title}")
        show(title, result)

    print("\n=== 多轮记忆(Memory/Context) ===")
    session = "demo-memory"
    turn1 = runtime.run("帮我查一下订单", user_id="U10001", session_id=session)
    show("第1轮", turn1)
    turn2 = runtime.run("昨天买的那个到哪了", user_id="U10001", session_id=session)
    show("第2轮(结合上下文)", turn2)

    print(f"\nTrace 已保存到 {TRACE_DIR / 'agent_traces.jsonl'}")


if __name__ == "__main__":
    main()
