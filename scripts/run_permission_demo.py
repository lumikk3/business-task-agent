#!/usr/bin/env python3
"""Step 11 演示：工具权限系统（Agent -> Permission Manager -> YES/CONFIRM/NO）。

同一笔「退款」，金额不同 → 走三条不同的路径：

    < 100 元   -> AUTO     直接执行
    100~500 元 -> CONFIRM  挂起,等用户确认（human-in-the-loop）
    > 500 元   -> HUMAN    转人工审批,不执行

用注入式 StubBrain 指定要调用的工具,不需要 LLM。

    python scripts/run_permission_demo.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.agent.brain import Decision  # noqa: E402
from app.agent.permissions import PermissionPolicy  # noqa: E402
from app.agent.runtime import AgentRuntime  # noqa: E402
from app.data.generator import DEFAULT_DB_PATH, generate  # noqa: E402
from app.memory.context import MemoryStore  # noqa: E402
from app.rag.policy_rag import PolicyRAG  # noqa: E402
from app.store import open_scratch_store  # noqa: E402
from app.tools.business import BusinessTools, build_registry  # noqa: E402
from app.tools.registry import ToolExecutor  # noqa: E402


class StubBrain:
    """只为演示权限门：固定请求一次 create_refund_request。"""

    def __init__(self, order_id: str, amount: float):
        self._decision = Decision("tool", tool="create_refund_request",
                                  arguments={"order_id": order_id, "amount": amount},
                                  reason=f"用户申请退款{amount}元")
        self._done = False

    def decide(self, ctx):
        if not self._done:
            self._done = True
            return self._decision
        return Decision("answer", answer="已处理完成。")


def build(brain, order_id: str = "O10003"):
    if not Path(DEFAULT_DB_PATH).exists():
        generate(DEFAULT_DB_PATH)
    store = open_scratch_store(str(DEFAULT_DB_PATH))
    tools = BusinessTools(store, policy_search=PolicyRAG().retrieve)
    registry = build_registry(tools)
    executor = ToolExecutor(registry, max_retries=1, timeout_s=3.0, sleep=lambda _s: None)
    return AgentRuntime(brain, registry, executor, MemoryStore(),
                        permission_policy=PermissionPolicy(auto_max=100, confirm_max=500))


def main() -> None:
    # 订单 O10003 实付 299 元,所以三档金额都选在不超过订单金额的区间内
    for amount in (50, 250, 900):
        runtime = build(StubBrain("O10003", amount))
        session = f"perm-{amount}"
        result = runtime.run(f"我要退款{amount}元", user_id="U10003", session_id=session)

        decision = ("CONFIRM" if result.status == "awaiting_confirmation"
                    else "HUMAN" if result.status == "escalated" else "AUTO")
        print(f"\n金额 {amount} 元  ->  {decision}")
        print(f"  首轮状态 : {result.status}")
        if result.pending:
            print(f"  权限门   : {result.pending['reason']}")
        if result.status == "awaiting_confirmation":
            print(f"  挂起提示 : {result.final_response}")
            resumed = runtime.resume(session, confirm=True)
            print(f"  用户确认后: 工具链={[c['tool'] for c in resumed.tool_calls]} "
                  f"status={resumed.status}")
            print(f"  回答     : {resumed.final_response}")
        else:
            print(f"  工具链   : {[c['tool'] for c in result.tool_calls]}")
            print(f"  回答     : {result.final_response}")


if __name__ == "__main__":
    main()
