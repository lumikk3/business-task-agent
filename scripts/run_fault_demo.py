#!/usr/bin/env python3
"""Step 10 演示：工具失败下的 Retry -> Fallback -> 人工接管。

用完整版 AgentRuntime（自带 Retry/退避 + 失败回退人工工单），配合故障注入：

    Tool Call -> Timeout -> Retry -> Retry -> 仍失败 -> Fallback -> Human Ticket

    python scripts/run_fault_demo.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.agent.brain import RuleBasedBrain  # noqa: E402
from app.agent.runtime import AgentRuntime  # noqa: E402
from app.memory.context import MemoryStore  # noqa: E402
from app.rag.policy_rag import PolicyRAG  # noqa: E402
from app.store import reset_store  # noqa: E402
from app.tools.business import BusinessTools, build_registry  # noqa: E402
from app.tools.faults import DEFAULT_ERROR_RATE, DEFAULT_TIMEOUT_RATE, inject_faults  # noqa: E402
from app.tools.registry import ToolExecutor  # noqa: E402

SEED = 20260927
READ_TOOLS = ("query_order", "query_logistics", "search_after_sales_policy")
QUESTION = "耳机坏了,我要退货。"


class RecordingExecutor(ToolExecutor):
    """记录每次工具调用的结果，方便把 Retry 链路打出来。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.log: list[dict] = []

    def execute(self, name, arguments=None):
        outcome = super().execute(name, arguments)
        self.log.append({
            "tool": name, "ok": outcome.ok, "attempts": outcome.attempts,
            "error_type": outcome.error_type.value if outcome.error_type else None,
        })
        return outcome


def build(store, timeout_rate, error_rate, tools=READ_TOOLS):
    business = BusinessTools(store, policy_search=PolicyRAG().retrieve)
    registry = build_registry(business)
    faulty = inject_faults(registry, timeout_rate=timeout_rate,
                           error_rate=error_rate, seed=SEED, tools=tools)
    executor = RecordingExecutor(faulty, max_retries=2, timeout_s=3.0,
                                 sleep=lambda _s: None)
    runtime = AgentRuntime(RuleBasedBrain(), faulty, executor, MemoryStore())
    return runtime, executor


def part_c_hard_failure():
    print("\n[Part C] 强制 query_order 100% 超时 —— 看完整链路")
    runtime, executor = build(reset_store(), timeout_rate=1.0, error_rate=0.0,
                              tools=("query_order",))
    result = runtime.run(QUESTION, user_id="U10001")
    print("  调用链:")
    for entry in executor.log:
        status = "ok" if entry["ok"] else f"FAIL({entry['error_type']})"
        print(f"    {entry['tool']:<26} attempts={entry['attempts']}  -> {status}")
    print(f"  状态   : {result.status}")
    print(f"  回答   : {result.final_response}")


def part_b_random_faults(runs: int):
    print(f"\n[Part B] {DEFAULT_TIMEOUT_RATE:.0%} timeout / {DEFAULT_ERROR_RATE:.0%} error,"
          f"连跑 {runs} 次")
    store = reset_store()
    runtime, executor = build(store, DEFAULT_TIMEOUT_RATE, DEFAULT_ERROR_RATE)
    retried = exhausted = completed = escalated = 0
    for _ in range(runs):
        executor.log.clear()
        result = runtime.run(QUESTION, user_id="U10001")
        if any(e["attempts"] > 1 for e in executor.log):
            retried += 1
        if any(not e["ok"] for e in executor.log):
            exhausted += 1
        if result.status == "completed":
            completed += 1
        else:
            escalated += 1
    print(f"  出现重试的运行     : {retried}/{runs}")
    print(f"  有 tool 重试后仍失败: {exhausted}/{runs}")
    print(f"  最终完成           : {completed}/{runs}")
    print(f"  失败转人工         : {escalated}/{runs}")


def part_a_baseline():
    print("[Part A] 不注入故障（基线）")
    runtime, _ = build(reset_store(), timeout_rate=0.0, error_rate=0.0)
    result = runtime.run(QUESTION, user_id="U10001")
    print(f"  状态   : {result.status}")
    print(f"  回答   : {result.final_response}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=20)
    args = parser.parse_args()
    part_a_baseline()
    part_b_random_faults(args.runs)
    part_c_hard_failure()


if __name__ == "__main__":
    main()
