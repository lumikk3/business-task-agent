"""最小版 Agent 测试（离线）：只 3 个工具、无 Planner、标准 function-calling 循环。

用一个本地假的 chat-completions server 驱动，不需要网络或 API Key。
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.agent.llm_client import ChatClient, LLMConfig
from app.agent.minimal_agent import MinimalAgent
from app.rag.policy_rag import PolicyRAG
from app.store import reset_store
from app.tools.business import BusinessTools, build_minimal_registry
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


def _agent(base_url: str, max_steps: int = 6) -> MinimalAgent:
    tools = BusinessTools(reset_store(), policy_search=PolicyRAG().retrieve)
    registry = build_minimal_registry(tools)
    executor = ToolExecutor(registry, max_retries=1, timeout_s=2.0, sleep=lambda _s: None)
    client = ChatClient(LLMConfig(base_url=base_url, api_key="test", model="glm-4-flash"))
    return MinimalAgent(client, registry, executor, max_steps=max_steps)


def test_minimal_registry_exposes_only_three_tools():
    tools = BusinessTools(reset_store(), policy_search=PolicyRAG().retrieve)
    registry = build_minimal_registry(tools)
    assert set(registry.names()) == {
        "query_order", "query_logistics", "search_after_sales_policy"}


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

    # 没有 Planner：步骤里不应出现 plan 节点
    assert {s["type"] for s in result.steps} <= {"user", "tool", "answer"}


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
