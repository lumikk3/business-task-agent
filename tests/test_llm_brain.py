"""OpenAIBrain tests against a local fake chat-completions server."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.agent.llm_brain import OpenAIBrain, has_llm_credentials
from app.agent.llm_client import resolve_llm_config
from app.memory.context import MemoryStore
from app.rag.policy_rag import PolicyRAG
from app.store import reset_store
from app.tools.business import BusinessTools, build_registry


class _Handler(BaseHTTPRequestHandler):
    queue: list = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        payload = _Handler.queue.pop(0)
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # keep test output clean
        pass


@pytest.fixture
def fake_llm():
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/v1"
    server.shutdown()
    _Handler.queue.clear()


def _make_brain(base_url: str) -> OpenAIBrain:
    tools = BusinessTools(reset_store(), policy_search=PolicyRAG().retrieve)
    registry = build_registry(tools)
    return OpenAIBrain(registry, base_url=base_url, api_key="test-key",
                       model="test-model")


def _ctx():
    memory = MemoryStore()
    return memory.start_turn("U10001", "s1", "我的耳机坏了,我要退货")


def test_llm_brain_parses_tool_calls(fake_llm):
    _Handler.queue.append({
        "choices": [{"message": {"tool_calls": [{
            "function": {"name": "query_order", "arguments": '{"user_id": "U10001"}'},
        }]}}],
        "usage": {"total_tokens": 42},
    })
    brain = _make_brain(fake_llm)
    decision = brain.decide(_ctx())
    assert decision.kind == "tool"
    assert decision.tool == "query_order"
    assert decision.arguments == {"user_id": "U10001"}


def test_llm_brain_parses_final_answer(fake_llm):
    _Handler.queue.append({
        "choices": [{"message": {"content": "退货申请已创建。"}}],
        "usage": {"total_tokens": 7},
    })
    brain = _make_brain(fake_llm)
    ctx = _ctx()
    decision = brain.decide(ctx)
    assert decision.kind == "answer"
    assert decision.answer == "退货申请已创建。"
    assert ctx.slots["tokens"] == 7


def test_has_llm_credentials_respects_env(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("MIMO_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert has_llm_credentials() is False
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert has_llm_credentials() is True


def test_llm_variables_are_the_single_switch(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "mimo-test")
    monkeypatch.setenv("LLM_BASE_URL", "https://mimo.example/v1")
    monkeypatch.setenv("LLM_MODEL", "mimo-test-model")
    monkeypatch.setenv("MIMO_API_KEY", "legacy-key")
    monkeypatch.setenv("MIMO_MODEL", "legacy-model")

    config = resolve_llm_config()

    assert config.api_key == "mimo-test"
    assert config.base_url == "https://mimo.example/v1"
    assert config.model == "mimo-test-model"


# ---- 传输层重试（真实遇到过：端点分块响应被截断 -> IncompleteRead 崩掉 Demo）----
def test_post_retries_transient_transport_error(monkeypatch):
    import http.client

    import app.agent.llm_client as lc

    calls = {"n": 0}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b'{"ok": true}'

    def fake_urlopen(request, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise http.client.IncompleteRead(b"53 bytes")   # 模拟响应被中途截断
        return _Resp()

    monkeypatch.setattr(lc.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(lc.time, "sleep", lambda _seconds: None)

    client = lc.ChatClient(lc.LLMConfig(base_url="http://x/v1", api_key="k", model="m"))
    assert client.chat([{"role": "user", "content": "hi"}]) == {"ok": True}
    assert calls["n"] == 2, "偶发传输错误应重试后成功"


def test_post_does_not_retry_on_4xx(monkeypatch):
    import io
    import urllib.error

    import app.agent.llm_client as lc

    calls = {"n": 0}

    def fake_urlopen(request, timeout=None):
        calls["n"] += 1
        raise urllib.error.HTTPError(
            "http://x/v1/chat/completions", 400, "Bad Request", {},
            io.BytesIO(b'{"error":"bad request"}'))

    monkeypatch.setattr(lc.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(lc.time, "sleep", lambda _seconds: None)

    client = lc.ChatClient(lc.LLMConfig(base_url="http://x/v1", api_key="k", model="m"))
    with pytest.raises(lc.LLMError):
        client.chat([{"role": "user", "content": "hi"}])
    assert calls["n"] == 1, "请求本身有问题时不要重试"
