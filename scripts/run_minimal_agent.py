#!/usr/bin/env python3
"""最小版 Agent 演示（Step 3 / Step 4）：三个工具 + 三个问题。

    python scripts/run_minimal_agent.py

循环：User -> LLM(GLM) -> Tool -> Tool Result -> LLM -> Answer，没有 Planner。

需要 GLM_API_KEY（默认读 ~/.hermes/.env 或环境变量，见 app/agent/llm_client.py）。
业务数据来自 data/business.db，缺失时会自动生成。
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
from app.store import open_store  # noqa: E402
from app.tools.business import MINIMAL_TOOLS, BusinessTools, build_minimal_registry  # noqa: E402
from app.rag.policy_rag import PolicyRAG  # noqa: E402
from app.tools.registry import ToolExecutor  # noqa: E402

# 三个问题 -> 对应演示用户（见 app/data/generator.py::DEMO_USERS）
QUESTIONS = [
    ("U10001", "我的订单什么时候发货?"),
    ("U10002", "我的快递到哪里了?"),
    ("U10003", "这个耳机能退吗?"),
]


def build_agent(db_path: str, model: str | None = None) -> MinimalAgent:
    if not Path(db_path).exists():
        print(f"[data] {db_path} 不存在，自动生成业务数据 …")
        generate(db_path)
    store = open_store(db_path)
    tools = BusinessTools(store, policy_search=PolicyRAG().retrieve)
    registry = build_minimal_registry(tools)
    executor = ToolExecutor(registry, max_retries=2, timeout_s=5.0)
    config = resolve_llm_config(model=model)
    print(f"[llm] model={config.model} base_url={config.base_url}")
    print(f"[tools] {', '.join(registry.names())}")
    return MinimalAgent(ChatClient(config), registry, executor)


def show(index: int, user_id: str, question: str, result) -> None:
    print(f"\n=== 问题{index}｜{user_id}｜{question} ===")
    for step in result.steps:
        if step["type"] == "tool":
            mark = "ok" if step["ok"] else f"FAIL({step['error']})"
            print(f"  step{step['step']}  tool  {step['tool']}({step['arguments']})  "
                  f"-> {mark}  {step['latency_ms']}ms")
    print(f"  工具链 : {' -> '.join(c.name for c in result.tool_calls) or '(无)'}")
    print(f"  状态   : {result.status}   轮次: {result.iterations}    tokens: {result.tokens}")
    print(f"  回答   : {result.answer}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--model", default=None, help="覆盖模型名 (默认 glm-4-flash)")
    args = parser.parse_args()

    if not has_llm_credentials():
        print("[error] 未找到 GLM_API_KEY / OPENAI_API_KEY，无法调用 LLM。")
        print("        可 export GLM_API_KEY=... 或写入 ~/.hermes/.env 后重试。")
        raise SystemExit(2)

    agent = build_agent(args.db, model=args.model)
    for i, (user_id, question) in enumerate(QUESTIONS, start=1):
        result = agent.run(question, user_id=user_id)
        show(i, user_id, question, result)


if __name__ == "__main__":
    main()
