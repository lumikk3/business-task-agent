"""LLM brain — OpenAI-compatible function calling (DESIGN.md 9 V1).

Same `decide(ctx) -> Decision` contract as RuleBasedBrain, but the next action
is chosen by an LLM through the tool schemas from the registry. Used when
OPENAI_API_KEY is configured; the demo defaults to the deterministic brain.
"""
from __future__ import annotations

import json
import os
import urllib.request

from app.agent.brain import Decision
from app.memory.context import TaskContext
from app.tools.registry import ToolRegistry

_SYSTEM_PROMPT = (
    "你是电商售后客服Agent。根据用户问题和已有的工具执行结果,决定下一步:"
    "调用一个工具,或直接给出最终回答。"
    "处理退货时必须先查询订单和售后政策再决定是否创建退货申请;"
    "高风险操作(大额退款、超期退货、政策冲突)不要直接执行,应转人工。"
)


def has_llm_credentials() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY"))


class OpenAIBrain:
    def __init__(self, registry: ToolRegistry, base_url: str | None = None,
                 api_key: str | None = None, model: str | None = None,
                 timeout_s: float = 60.0):
        self.registry = registry
        self.base_url = (base_url or os.environ.get("OPENAI_BASE_URL")
                         or "https://api.openai.com/v1").rstrip("/")
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.model = model or os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        self.timeout_s = timeout_s

    # ------------------------------------------------------------------
    def decide(self, ctx: TaskContext) -> Decision:
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": self._build_user_message(ctx)},
        ]
        body = {
            "model": self.model,
            "messages": messages,
            "tools": self.registry.schemas(),
        }
        response = self._chat(body)
        message = response["choices"][0]["message"]
        tokens = (response.get("usage") or {}).get("total_tokens", 0)
        ctx.slots["tokens"] = int(ctx.slots.get("tokens", 0)) + int(tokens)
        tool_calls = message.get("tool_calls") or []
        if tool_calls:
            call = tool_calls[0]["function"]
            try:
                arguments = json.loads(call.get("arguments") or "{}")
            except json.JSONDecodeError:
                arguments = {}
            return Decision("tool", tool=call["name"], arguments=arguments,
                            reason="LLM选择工具")
        content = message.get("content") or ""
        if content.strip().startswith("{"):  # LLM may emit {"tool": ...} as text
            try:
                payload = json.loads(content)
                if payload.get("tool"):
                    return Decision("tool", tool=payload["tool"],
                                    arguments=payload.get("arguments") or {},
                                    reason="LLM选择工具(JSON)")
            except json.JSONDecodeError:
                pass
        return Decision("answer", answer=content)

    # ------------------------------------------------------------------
    def _build_user_message(self, ctx: TaskContext) -> str:
        lines = [f"用户ID:{ctx.user_id}", f"用户输入:{ctx.user_input}"]
        if ctx.intent:
            lines.append(f"意图识别结果:{ctx.intent}")
        if ctx.plan:
            lines.append(f"执行计划:{' -> '.join(ctx.plan)}")
        if ctx.observations:
            lines.append("已有工具结果:")
            lines.append(json.dumps(ctx.observations, ensure_ascii=False))
        lines.append("请决定下一步:返回tool_calls调用工具,或直接输出最终回答。")
        return "\n".join(lines)

    def _chat(self, body: dict) -> dict:
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout_s) as resp:
            return json.loads(resp.read().decode("utf-8"))
