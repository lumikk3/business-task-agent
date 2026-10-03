"""最小版 Agent（Step 3 / Step 4）—— 没有 Planner。

唯一的核心循环就是设计里那一张图：

    User -> LLM -> Tool -> Tool Result -> LLM -> Answer

不生成执行计划、不做权限门、不做 RAG 之外的编排；LLM 通过 function calling
自己决定调哪个工具，工具结果以标准 ``role: tool`` 消息回灌，直到模型给出最终回答。

对比完整版 ``AgentRuntime``（含 Planner / 权限 / 人工接管 / Trace），这里是刻意的
最小实现，用来先跑通第一个可展示的 Agent。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from app.tools.registry import ToolExecutor, ToolRegistry

DEFAULT_SYSTEM_PROMPT = """你是电商售后客服 Agent。你可以调用工具查询业务系统来回答用户问题。

可用工具:
- query_order(user_id): 查询用户的订单,包含商品、订单状态、售后与退款记录
- query_logistics(order_id): 查询订单的物流状态、承运商与最新轨迹
- search_after_sales_policy(query): 检索售后政策知识库(退货、退款、运费、人工审核规则)

规则:
1. 先调用工具拿真实数据再回答,不要编造订单号、物流或政策内容。
2. 用户消息里会给出用户ID。
3. 问物流时先用 query_order 找到订单,再用 query_logistics 查物流。
4. 问"能不能退/售后政策"时先用 query_order 拿到商品与签收时间,再用
   search_after_sales_policy 检索政策,结合签收天数给出结论。
5. 信息足够后直接给出简洁的中文最终回答,不要再调用工具。"""


@dataclass
class ToolCall:
    name: str
    arguments: dict
    ok: bool
    result: dict | None = None
    error: str | None = None
    latency_ms: float = 0.0
    attempts: int = 1


@dataclass
class MinimalResult:
    user_input: str
    user_id: str | None
    answer: str
    status: str                       # completed | max_steps | error
    tool_calls: list[ToolCall] = field(default_factory=list)
    steps: list[dict] = field(default_factory=list)
    iterations: int = 0
    tokens: int = 0

    def to_dict(self) -> dict:
        return {
            "user_input": self.user_input,
            "user_id": self.user_id,
            "answer": self.answer,
            "status": self.status,
            "iterations": self.iterations,
            "tokens": self.tokens,
            "tool_calls": [
                {"tool": c.name, "arguments": c.arguments, "ok": c.ok,
                 "latency_ms": round(c.latency_ms, 2), "attempts": c.attempts,
                 "error": c.error}
                for c in self.tool_calls
            ],
            "steps": self.steps,
        }


class MinimalAgent:
    def __init__(self, client, registry: ToolRegistry, executor: ToolExecutor,
                 system_prompt: str = DEFAULT_SYSTEM_PROMPT, max_steps: int = 6):
        self.client = client
        self.registry = registry
        self.executor = executor
        self.system_prompt = system_prompt
        self.max_steps = max_steps

    # ------------------------------------------------------------------
    def run(self, user_input: str, user_id: str | None = None) -> MinimalResult:
        user_content = f"用户ID: {user_id}\n用户问题: {user_input}" if user_id else user_input
        messages: list[dict] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_content},
        ]
        result = MinimalResult(user_input=user_input, user_id=user_id,
                               answer="", status="error")
        result.steps.append({"step": 0, "type": "user", "content": user_content})
        schemas = self.registry.schemas()

        for step in range(1, self.max_steps + 1):
            result.iterations = step
            response = self.client.chat(messages, tools=schemas)
            result.tokens += int((response.get("usage") or {}).get("total_tokens", 0))

            message = response["choices"][0]["message"]
            tool_calls = message.get("tool_calls") or []

            if tool_calls:
                messages.append({
                    "role": "assistant",
                    "content": message.get("content") or "",
                    "tool_calls": tool_calls,
                })
                for call in tool_calls:
                    self._run_tool_call(call, messages, result, step)
                continue

            result.answer = (message.get("content") or "").strip()
            result.status = "completed"
            result.steps.append({"step": step, "type": "answer", "content": result.answer})
            return result

        result.answer = "（达到最大步数仍未得到最终回答）"
        result.status = "max_steps"
        result.steps.append({"step": self.max_steps, "type": "answer", "content": result.answer})
        return result

    # ------------------------------------------------------------------
    def _run_tool_call(self, call: dict, messages: list[dict],
                       result: MinimalResult, step: int) -> None:
        function = call.get("function") or {}
        name = function.get("name") or ""
        try:
            arguments = json.loads(function.get("arguments") or "{}")
        except json.JSONDecodeError:
            arguments = {}

        outcome = self.executor.execute(name, arguments)
        record = ToolCall(
            name=name, arguments=arguments, ok=outcome.ok,
            result=outcome.data if outcome.ok else None,
            error=outcome.error, latency_ms=outcome.latency_ms, attempts=outcome.attempts,
        )
        result.tool_calls.append(record)
        result.steps.append({
            "step": step, "type": "tool", "tool": name, "arguments": arguments,
            "ok": outcome.ok, "latency_ms": round(outcome.latency_ms, 2),
            "attempts": outcome.attempts, "error": outcome.error,
        })

        # 工具结果以 role: tool 回灌给 LLM —— 这就是图里的 "Tool Result"
        messages.append({
            "role": "tool",
            "tool_call_id": call.get("id"),
            "content": json.dumps(outcome.to_dict(), ensure_ascii=False),
        })
