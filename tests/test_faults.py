"""故障注入测试（Step 10）：Retry -> Fallback -> 人工工单。"""
from __future__ import annotations

from app.agent.runtime import AgentRuntime
from app.agent.brain import RuleBasedBrain
from app.memory.context import MemoryStore
from app.rag.policy_rag import PolicyRAG
from app.store import reset_store
from app.tools.business import BusinessTools, build_registry
from app.tools.faults import inject_faults
from app.tools.registry import ErrorType, ToolExecutor

READ_TOOLS = ("query_order", "query_logistics", "search_after_sales_policy")


def _runtime(registry):
    executor = ToolExecutor(registry, max_retries=2, timeout_s=3.0, sleep=lambda _s: None)
    return AgentRuntime(RuleBasedBrain(), registry, executor, MemoryStore()), executor


def _business_registry():
    tools = BusinessTools(reset_store(), policy_search=PolicyRAG().retrieve)
    return build_registry(tools)


def test_inject_faults_does_not_mutate_original_registry():
    original = _business_registry()
    before = original.get("query_order").handler
    inject_faults(original, timeout_rate=1.0, tools=("query_order",))
    assert original.get("query_order").handler is before   # 原注册表未被改动


def test_zero_rate_keeps_tools_healthy():
    faulty = inject_faults(_business_registry(), timeout_rate=0.0, error_rate=0.0)
    executor = ToolExecutor(faulty, sleep=lambda _s: None)
    assert executor.execute("query_order", {"user_id": "U10001"}).ok is True


def test_full_timeout_exhausts_retries_then_falls_back_to_ticket():
    faulty = inject_faults(_business_registry(), timeout_rate=1.0, error_rate=0.0,
                           tools=("query_order",))          # human ticket 保持健康
    runtime, executor = _runtime(faulty)
    result = runtime.run("耳机坏了,我要退货。", user_id="U10001")

    assert result.status == "escalated"
    assert "工单T" in result.final_response
    assert result.tool_calls[-1]["tool"] == "create_human_ticket"

    failed = executor.execute("query_order", {"user_id": "U10001"})
    assert failed.ok is False
    assert failed.error_type == ErrorType.TIMEOUT
    assert failed.attempts == 3                              # 首次 + 2 次重试


def test_faults_only_apply_to_selected_tools():
    faulty = inject_faults(_business_registry(), timeout_rate=1.0, error_rate=0.0,
                           tools=("query_order",))
    executor = ToolExecutor(faulty, sleep=lambda _s: None)
    # 未选中的工具不受影响
    assert executor.execute("query_logistics", {"order_id": "O202609004"}).ok is True


def test_random_faults_are_seeded_and_reproducible():
    def run_once():
        faulty = inject_faults(_business_registry(), timeout_rate=0.5, error_rate=0.1,
                               seed=7, tools=READ_TOOLS)
        executor = ToolExecutor(faulty, max_retries=0, sleep=lambda _s: None)
        return [executor.execute("query_order", {"user_id": "U10001"}).ok for _ in range(10)]

    assert run_once() == run_once()
