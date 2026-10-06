"""最小版 Agent 测试（离线）：只 3 个工具、无 Planner、标准 function-calling 循环。

用一个本地假的 chat-completions server 驱动，不需要网络或 API Key。
"""
from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.agent.llm_client import ChatClient, LLMConfig, _load_env_file
from app.agent.minimal_agent import MinimalAgent, build_system_prompt
from app.agent.permissions import PermissionPolicy
from app.memory.session import SessionMemory
from app.rag.policy_rag import PolicyRAG
from app.store import reset_store
from app.tools.business import (
    MINIMAL_TOOLS,
    RETURN_TOOLS,
    BusinessTools,
    build_minimal_registry,
    build_registry,
)
from app.tools.registry import ToolExecutor

REQUESTS: list[dict] = []


class _Handler(BaseHTTPRequestHandler):
    queue: list = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        REQUESTS.append(json.loads(self.rfile.read(length).decode("utf-8")))
        payload = _Handler.queue.pop(0)
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def fake_llm():
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    REQUESTS.clear()
    _Handler.queue.clear()
    yield f"http://127.0.0.1:{server.server_address[1]}/v1"
    server.shutdown()
    _Handler.queue.clear()


def _tool_call(name: str, arguments: dict, call_id: str = "call_1") -> dict:
    return {"choices": [{"message": {
        "role": "assistant", "content": "",
        "tool_calls": [{"id": call_id, "type": "function", "function": {
            "name": name, "arguments": json.dumps(arguments)}}],
    }}], "usage": {"total_tokens": 10}}


def _answer(text: str) -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": text}}],
            "usage": {"total_tokens": 5}}


def _agent(base_url: str, max_steps: int = 6, memory=None,
           names=MINIMAL_TOOLS) -> MinimalAgent:
    tools = BusinessTools(reset_store(), policy_search=PolicyRAG().retrieve)
    registry = build_minimal_registry(tools, names)
    executor = ToolExecutor(registry, max_retries=1, timeout_s=2.0, sleep=lambda _s: None)
    client = ChatClient(LLMConfig(base_url=base_url, api_key="test", model="glm-4.6"))
    return MinimalAgent(client, registry, executor, max_steps=max_steps, memory=memory)


def test_minimal_registry_exposes_only_three_tools():
    tools = BusinessTools(reset_store(), policy_search=PolicyRAG().retrieve)
    registry = build_minimal_registry(tools)
    assert set(registry.names()) == {
        "query_order", "query_logistics", "search_after_sales_policy"}


def test_step5_registry_adds_create_return_request():
    tools = BusinessTools(reset_store(), policy_search=PolicyRAG().retrieve)
    registry = build_minimal_registry(tools, RETURN_TOOLS)
    assert "create_return_request" in registry.names()
    assert registry.risk_level("create_return_request").value == "MEDIUM"


def test_load_env_file_supports_project_dotenv(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text("GLM_API_KEY=glm-test\nOPENAI_MODEL=gpt-4o-mini\n", encoding="utf-8")

    monkeypatch.delenv("GLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)

    _load_env_file(env_path)

    assert os.environ["GLM_API_KEY"] == "glm-test"
    assert os.environ["OPENAI_MODEL"] == "gpt-4o-mini"


def test_system_prompt_only_describes_available_tools():
    tools = BusinessTools(reset_store(), policy_search=PolicyRAG().retrieve)
    prompt_3 = build_system_prompt(build_minimal_registry(tools, MINIMAL_TOOLS))
    assert "query_order" in prompt_3
    assert "create_return_request" not in prompt_3     # 没提供的工具不该出现在提示词里

    prompt_4 = build_system_prompt(build_minimal_registry(tools, RETURN_TOOLS))
    assert "create_return_request" in prompt_4


def test_loop_tool_then_answer(fake_llm):
    _Handler.queue.append(_tool_call("query_order", {"user_id": "U10001"}))
    _Handler.queue.append(_answer("您的订单预计48小时内发货。"))

    result = _agent(fake_llm).run("我的订单什么时候发货?", user_id="U10001")

    assert result.status == "completed"
    assert result.answer == "您的订单预计48小时内发货。"
    assert [c.name for c in result.tool_calls] == ["query_order"]
    assert result.tool_calls[0].ok is True
    assert result.iterations == 2

    # 第二次请求里已经把工具结果以 role: tool 回灌了
    follow_up = REQUESTS[1]["messages"]
    tool_msgs = [m for m in follow_up if m["role"] == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["tool_call_id"] == "call_1"
    assert "O202609005" in tool_msgs[0]["content"]  # 种子数据里的订单

    # 没有 Planner：步骤里不应出现 plan 节点（permission 是 Step 11 的权限门）
    assert "plan" not in {s["type"] for s in result.steps}
    assert {s["type"] for s in result.steps} <= {"user", "tool", "answer", "permission"}


def test_unknown_tool_is_fed_back_not_fatal(fake_llm):
    _Handler.queue.append(_tool_call("query_weather", {"city": "上海"}))
    _Handler.queue.append(_answer("抱歉,该查询暂不支持。"))

    result = _agent(fake_llm).run("上海天气怎么样", user_id="U10001")

    assert result.status == "completed"
    assert result.tool_calls[0].ok is False
    # 工具失败以 role: tool 回灌,循环继续
    assert any(m["role"] == "tool" for m in REQUESTS[1]["messages"])
    assert result.answer == "抱歉,该查询暂不支持。"


def test_max_steps_stops_runaway_loop(fake_llm):
    for i in range(10):
        _Handler.queue.append(_tool_call("query_order", {"user_id": "U10001"},
                                         call_id=f"call_{i}"))
    result = _agent(fake_llm, max_steps=3).run("一直查订单", user_id="U10001")
    assert result.status == "max_steps"
    assert result.iterations == 3


# ---- Step 9: memory ---------------------------------------------------
def test_memory_carries_task_state_across_turns(fake_llm):
    memory = SessionMemory()
    agent = _agent(fake_llm, memory=memory)

    # 第1轮: 调 query_order,记忆里应落下当前订单
    _Handler.queue.append(_tool_call("query_order", {"user_id": "U10001"}))
    _Handler.queue.append(_answer("已查到您的订单。"))
    agent.run("我的耳机坏了。", user_id="U10001", session_id="s1")
    order_id = memory.slots("s1").get("order_id")
    assert order_id, "第1轮后应记住当前订单"

    # 第2轮: 用户用指代,注入的上下文里应带上当前订单
    REQUESTS.clear()
    _Handler.queue.append(_answer("就是那个订单。"))
    agent.run("就是昨天那个订单。", user_id="U10001", session_id="s1")
    user_msg = next(m for m in REQUESTS[0]["messages"] if m["role"] == "user")
    assert "[会话上下文]" in user_msg["content"]
    assert order_id in user_msg["content"]


# ---- Step 11: permission gate -----------------------------------------
def _full_agent(base_url, policy=None, memory=None, max_steps=6):
    """带全部 6 个工具（含 create_refund_request / create_human_ticket）的 agent。"""
    tools = BusinessTools(reset_store(), policy_search=PolicyRAG().retrieve)
    registry = build_registry(tools)
    executor = ToolExecutor(registry, max_retries=1, timeout_s=2.0, sleep=lambda _s: None)
    client = ChatClient(LLMConfig(base_url=base_url, api_key="test", model="glm-4.6"))
    return MinimalAgent(client, registry, executor, max_steps=max_steps, memory=memory,
                        permission_policy=policy)


def test_permission_auto_executes_read_and_return(fake_llm):
    """query_* 与 create_return 都是 AUTO —— 不打断流程。"""
    _Handler.queue.append(_tool_call("create_return_request",
                                     {"order_id": "O202609001", "reason": "耳机坏了"}, "c1"))
    _Handler.queue.append(_answer("退货申请已创建。"))
    result = _full_agent(fake_llm).run("耳机坏了,我要退货", user_id="U10001")

    assert result.status == "completed"
    modes = [s["mode"] for s in result.steps if s["type"] == "permission"]
    assert modes == ["auto"]
    assert result.tool_calls[0].name == "create_return_request"


def test_permission_confirm_pauses_then_resumes(fake_llm):
    """create_refund 中等金额 -> CONFIRM：先挂起,不执行;确认后再执行。"""
    _Handler.queue.append(_tool_call("create_refund_request",
                                     {"order_id": "O202609002", "amount": 300}, "c1"))
    agent = _full_agent(fake_llm)
    result = agent.run("我要退款300元", user_id="U10001", session_id="gate")

    assert result.status == "awaiting_confirmation"
    assert result.pending == {"tool": "create_refund_request",
                              "arguments": {"order_id": "O202609002", "amount": 300},
                              "reason": "金额300.0元需用户二次确认"}
    assert result.tool_calls == []                      # 确认前绝不能执行

    _Handler.queue.append(_answer("退款已提交。"))       # resume 后继续循环需要一次 LLM 响应
    resumed = agent.resume("gate", confirm=True)
    assert resumed.status == "completed"
    assert any(c.name == "create_refund_request" and c.ok for c in resumed.tool_calls)


def test_permission_confirm_can_be_cancelled(fake_llm):
    _Handler.queue.append(_tool_call("create_refund_request",
                                     {"order_id": "O202609002", "amount": 300}, "c1"))
    agent = _full_agent(fake_llm)
    agent.run("我要退款300元", user_id="U10001", session_id="gate2")
    result = agent.resume("gate2", confirm=False)
    assert result.status == "cancelled"
    assert result.tool_calls == []                      # 取消后没有执行任何写操作


def test_permission_human_approval_escalates_to_ticket(fake_llm):
    """大额退款 -> HUMAN_APPROVAL：直接转人工,不执行退款。"""
    _Handler.queue.append(_tool_call("create_refund_request",
                                     {"order_id": "O202609002", "amount": 900}, "c1"))
    result = _full_agent(fake_llm).run("我要退款900元", user_id="U10001")

    assert result.status == "escalated"
    assert "工单T" in result.answer
    assert not any(c.name == "create_refund_request" for c in result.tool_calls)
    assert result.tool_calls[-1].name == "create_human_ticket"


def test_permission_thresholds_are_configurable(fake_llm):
    """阈值是业务配置：把 confirm_max 压到 200 后,300 元直接转人工。"""
    policy = PermissionPolicy(auto_max=50, confirm_max=200)
    _Handler.queue.append(_tool_call("create_refund_request",
                                     {"order_id": "O202609002", "amount": 300}, "c1"))
    result = _full_agent(fake_llm, policy=policy).run("我要退款300元", user_id="U10001")
    assert result.status == "escalated"


# ---- Step 19: 同一轮多工具并发 -----------------------------------------
def test_parallel_batch_executes_all_calls_in_one_round(fake_llm):
    _Handler.queue.append({"choices": [{"message": {
        "role": "assistant", "content": "",
        "tool_calls": [
            {"id": "c1", "type": "function", "function": {
                "name": "query_order", "arguments": '{"user_id": "U10001"}'}},
            {"id": "c2", "type": "function", "function": {
                "name": "query_logistics", "arguments": '{"order_id": "O202609004"}'}},
        ],
    }}], "usage": {"total_tokens": 12}})
    _Handler.queue.append(_answer("订单和物流都查到了。"))

    agent = _agent(fake_llm)
    agent.parallel_tools = True
    result = agent.run("查订单和物流", user_id="U10001")

    assert result.status == "completed"
    assert [c.name for c in result.tool_calls] == ["query_order", "query_logistics"]
    # 两个工具结果都必须按原顺序回灌,且带正确的 tool_call_id
    tool_msgs = [m for m in REQUESTS[1]["messages"] if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tool_msgs] == ["c1", "c2"]
