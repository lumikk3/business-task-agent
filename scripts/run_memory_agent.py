#!/usr/bin/env python3
"""Step 9 演示：Context / Memory —— 让 Agent 记住任务状态。

两轮对话，第二轮用「那个订单」这类指代，靠会话记忆里的当前订单/商品解析：

    第1轮: 我的耳机坏了。
    第2轮: 就是昨天那个订单。

    python scripts/run_memory_agent.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.agent.llm_client import ChatClient, has_llm_credentials, resolve_llm_config  # noqa: E402
from app.agent.minimal_agent import MinimalAgent  # noqa: E402
from app.data.generator import DEFAULT_DB_PATH, generate  # noqa: E402
from app.memory.session import SessionMemory  # noqa: E402
from app.rag.policy_rag import PolicyRAG  # noqa: E402
from app.store import open_scratch_store  # noqa: E402
from app.tools.business import RETURN_TOOLS, BusinessTools, build_minimal_registry  # noqa: E402
from app.tools.registry import ToolExecutor  # noqa: E402

TURNS = [
    ("U10003", "我的耳机坏了。"),
    ("U10003", "就是昨天那个订单。"),
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--model", default=None)
    args = parser.parse_args()

    if not has_llm_credentials():
        print("[error] 未找到 GLM_API_KEY / OPENAI_API_KEY，无法调用 LLM。")
        raise SystemExit(2)

    if not Path(args.db).exists():
        generate(args.db)
    store = open_scratch_store(args.db)
    tools = BusinessTools(store, policy_search=PolicyRAG().retrieve)
    registry = build_minimal_registry(tools, RETURN_TOOLS)
    executor = ToolExecutor(registry, max_retries=2, timeout_s=5.0)

    memory = SessionMemory()                 # 第一版：Python dict（可换 Redis）
    agent = MinimalAgent(ChatClient(resolve_llm_config(model=args.model)),
                         registry, executor, memory=memory)
    session = "memory-demo"

    for i, (user_id, text) in enumerate(TURNS, start=1):
        context = memory.context_block(session)
        print(f"\n=== 第{i}轮｜{user_id}｜{text} ===")
        print(f"  注入的会话上下文 : {context or '(空)'}")
        result = agent.run(text, user_id=user_id, session_id=session)
        chain = " -> ".join(c.name for c in result.tool_calls) or "(无)"
        print(f"  工具链           : {chain}")
        print(f"  回答             : {result.answer}")
        print(f"  更新后的槽位     : {memory.slots(session)}")


if __name__ == "__main__":
    main()
