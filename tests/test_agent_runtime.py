"""End-to-end tests for the Agent Runtime: the six scenarios plus the
error / permission / human-in-the-loop engineering paths."""
from __future__ import annotations

import pytest

from app.agent.brain import Decision, RuleBasedBrain
from app.agent.permissions import PermissionPolicy
from app.agent.runtime import AgentRuntime
from app.memory.context import MemoryStore
from app.rag.policy_rag import PolicyRAG
from app.store import reset_store
from app.tools.business import BusinessTools, build_registry
from app.tools.registry import ErrorType, RiskLevel, ToolError, ToolExecutor


class StubBrain:
    """Feeds a scripted sequence of decisions, then answers."""

    def __init__(self, decisions):
        self._decisions = list(decisions)

    def decide(self, ctx):
        if self._decisions:
            return self._decisions.pop(0)
        return Decision("answer", answer="已处理完成")


def make_runtime(brain=None, tools_cls=BusinessTools, **runtime_kwargs):
    store = reset_store()
    rag = PolicyRAG()
    tools = tools_cls(store, policy_search=rag.retrieve)
    registry = build_registry(tools)
    executor = ToolExecutor(registry, max_retries=1, timeout_s=2.0,
                            sleep=lambda _s: None)
    runtime = AgentRuntime(brain or RuleBasedBrain(), registry, executor,
                           MemoryStore(), **runtime_kwargs)
    return runtime, store


# ---- the six core scenarios ------------------------------------------
def test_scenario_return_request_happy_path():
    runtime, store = make_runtime()
    result = runtime.run("耳机坏了,我要退货。", user_id="U10001")
    assert result.status == "completed"
    assert result.intent == "RETURN_REQUEST"
    assert [c["tool"] for c in result.tool_calls] == [
        "query_order", "search_after_sales_policy", "create_return_request"]
    assert "退货申请已创建" in result.final_response
    assert "R2026" in result.final_response
    assert store.get_after_sale("O202609001")  # request persisted


def test_scenario_high_value_overdue_return_escalates_to_human():
    runtime, store = make_runtime()
    result = runtime.run("耳机超过售后期限了,但确实坏了,我要退货退款,金额超过1000元。",
                         user_id="U10002")
    assert result.status == "escalated"
    assert [c["tool"] for c in result.tool_calls] == [
        "query_order", "search_after_sales_policy", "create_human_ticket"]
    assert "工单T" in result.final_response
    assert "人工" in result.final_response


def test_scenario_order_query():
    runtime, _store = make_runtime()
    result = runtime.run("我的订单什么时候发货?", user_id="U10001")
    assert result.status == "completed"
    assert result.intent == "ORDER_QUERY"
    assert "48小时" in result.final_response


def test_scenario_logistics_query():
    runtime, _store = make_runtime()
    result = runtime.run("我的快递到哪里了?", user_id="U10002")
    assert result.status == "completed"
    assert result.intent == "LOGISTICS_QUERY"
    assert "运输中" in result.final_response


def test_scenario_policy_consult():
    runtime, _store = make_runtime()
    result = runtime.run("耳机用了5天还能退吗?", user_id="U10001")
    assert result.status == "completed"
    assert result.intent == "AFTERSALE_POLICY"
    assert "《" in result.final_response
    assert "可以申请退货" in result.final_response


def test_scenario_refund_query():
    runtime, _store = make_runtime()
    result = runtime.run("商品已经退回去了,什么时候退款?", user_id="U10001")
    assert result.status == "completed"
    assert result.intent == "REFUND_QUERY"
    assert "RF202609001" in result.final_response


def test_unknown_input_asks_for_clarification():
    runtime, _store = make_runtime()
    result = runtime.run("今天天气怎么样", user_id="U10001")
    assert result.status == "completed"
    assert result.intent == "UNKNOWN"
    assert "没有理解" in result.final_response


# ---- memory / multi-turn ---------------------------------------------
def test_multi_turn_context_resolves_yesterday_reference():
    runtime, _store = make_runtime()
    session = "mem"
    turn1 = runtime.run("帮我查一下订单", user_id="U10001", session_id=session)
    assert turn1.status == "completed"
    turn2 = runtime.run("昨天买的那个到哪了", user_id="U10001", session_id=session)
    assert turn2.intent == "LOGISTICS_QUERY"
    assert "O202609005" in turn2.final_response   # order created "yesterday"
    assert "待发货" in turn2.final_response


# ---- permission control & human-in-the-loop --------------------------
def test_permission_policy_amount_gates():
    policy = PermissionPolicy(auto_max=100, confirm_max=500)
    assert policy.evaluate("create_refund_request", {"amount": 50},
                           RiskLevel.HIGH).mode == "auto"
    assert policy.evaluate("create_refund_request", {"amount": 300},
                           RiskLevel.HIGH).mode == "confirm"
    assert policy.evaluate("create_refund_request", {"amount": 900},
                           RiskLevel.HIGH).mode == "human"
    assert policy.evaluate("create_return_request", {"order_id": "O1"},
                           RiskLevel.MEDIUM).mode == "auto"
    assert policy.evaluate("mystery", {}, RiskLevel.CRITICAL).mode == "human"


def test_confirmation_gate_then_resume_executes():
    stub = StubBrain([Decision("tool", tool="create_refund_request",
                               arguments={"order_id": "O202609002", "amount": 300},
                               reason="用户申请退款")])
    runtime, store = make_runtime(brain=stub)
    result = runtime.run("我要退款300元", user_id="U10001", session_id="confirm-demo")
    assert result.status == "awaiting_confirmation"
    assert result.pending == {"tool": "create_refund_request",
                              "arguments": {"order_id": "O202609002", "amount": 300},
                              "reason": "用户申请退款"}
    refunds_before = [r for r in store.get_refunds("O202609002") if r["refund_id"] != "RF202609001"]
    assert refunds_before == []

    resumed = runtime.resume("confirm-demo", confirm=True)
    assert resumed.status == "completed"
    assert resumed.final_response == "已处理完成"
    created = [r for r in store.get_refunds("O202609002") if r["refund_id"] != "RF202609001"]
    assert len(created) == 1 and created[0]["amount"] == 300


def test_confirmation_gate_can_be_cancelled():
    stub = StubBrain([Decision("tool", tool="create_refund_request",
                               arguments={"order_id": "O202609002", "amount": 300})])
    runtime, store = make_runtime(brain=stub)
    runtime.run("我要退款300元", user_id="U10001", session_id="cancel-demo")
    result = runtime.resume("cancel-demo", confirm=False)
    assert result.status == "cancelled"
    assert "取消" in result.final_response


# ---- error handling / fallback ---------------------------------------
def test_tool_failure_falls_back_to_human_ticket():
    class BrokenTools(BusinessTools):
        def query_order(self, user_id, order_id=None):
            raise ToolError("order api timeout", ErrorType.TIMEOUT, retryable=True)

    runtime, _store = make_runtime(tools_cls=BrokenTools)
    result = runtime.run("耳机坏了,我要退货。", user_id="U10001")
    assert result.status == "escalated"
    assert "连续失败" in result.final_response
    assert "工单T" in result.final_response
    assert result.tool_calls[-1]["tool"] == "create_human_ticket"


# ---- trace -----------------------------------------------------------
def test_trace_records_full_execution_chain(tmp_path):
    runtime, _store = make_runtime(trace_dir=tmp_path)
    result = runtime.run("耳机坏了,我要退货。", user_id="U10001")
    trace_file = tmp_path / "agent_traces.jsonl"
    assert trace_file.exists()
    import json
    trace = json.loads(trace_file.read_text(encoding="utf-8").strip())
    assert trace["trace_id"] == result.trace_id
    nodes = [span["node"] for span in trace["spans"]]
    assert nodes[0] == "user_input"
    assert "intent" in nodes and "plan" in nodes and "decision" in nodes
    assert "permission" in nodes and "tool" in nodes
    assert nodes[-1] == "final"
    tool_span = next(s for s in trace["spans"] if s["node"] == "tool")
    assert tool_span["tool"] == "query_order"
    assert tool_span["arguments"] == {"user_id": "U10001"}
