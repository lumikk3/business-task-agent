"""最小版 Agent（Step 3~6 / Step 9）—— 没有 Planner。

唯一的核心循环就是设计里那一张图：

    User -> LLM -> Tool -> Tool Result -> LLM -> Answer

也就是 Think -> Act -> Observe -> Think -> Act ...：下一步调用什么工具**由 LLM
决定**，不是代码里写死的 ``query_order(); search_policy(); create_return()``。
工具结果以标准 ``role: tool`` 消息回灌，直到模型给出最终回答。

可选地挂一个 ``SessionMemory``（Step 9），让跨轮的任务状态（当前订单/商品）延续下去。

对比完整版 ``AgentRuntime``（含 Planner / 权限 / 人工接管 / Trace），这里是刻意的
最小实现。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from app.tools.registry import ToolExecutor, ToolRegistry

_PROMPT_HEADER = (
    "你是电商售后客服 Agent。你的目标是通过调用工具**完成**用户的业务任务,"
    "而不只是回答问题。\n\n可用工具:\n{tools}\n\n规则:\n{rules}"
)

_BASE_RULES = [
    "先调用工具拿真实数据再回答,不要编造订单号、物流或政策内容。",
    "用户消息里会给出用户ID,并可能带有 [会话上下文]。",
    "问物流:先用 query_order 找到订单,再用 query_logistics 查物流。",
    "问能不能退/售后政策:先用 query_order 拿商品与签收日期,"
    "再用 search_after_sales_policy 检索政策,结合签收天数给出结论。",
]

_RETURN_RULE = (
    "用户明确要求退货时,你要真正把任务办完,而不是只给建议:先 query_order 确认订单已签收"
    "并拿到签收日期,再用 search_after_sales_policy 确认仍在期限内(7天无理由 / 质量问题15天),"
    "满足条件就直接调用 create_return_request 创建退货申请。"
    "用户既然已经说要退货,就不要反问「是否需要为您创建」或索要多余确认,直接办理;"
    "只有在超期、金额过大或政策冲突时才只说明原因并建议人工客服。"
)


def build_system_prompt(registry: ToolRegistry) -> str:
    """按当前注册表动态生成提示词 —— 只描述真正可用的工具与规则。"""
    tools = "\n".join(f"- {spec.name}: {spec.description}" for spec in registry.specs())
    names = registry.names()
    rules = list(_BASE_RULES)
    if "create_return_request" in names:
        rules.append(_RETURN_RULE)
    if "create_human_ticket" in names:
        rules.append("风险过高、政策冲突或工具连续失败时,调用 create_human_ticket 转人工。")
    rules.append("信息足够后给出简洁的中文最终回答,不要再调用工具。")
    numbered = "\n".join(f"{i}. {rule}" for i, rule in enumerate(rules, start=1))
    return _PROMPT_HEADER.format(tools=tools, rules=numbered)


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
    session_id: str
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
            "session_id": self.session_id,
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
                 system_prompt: str | None = None, max_steps: int = 6,
                 memory=None):
        self.client = client
        self.registry = registry
        self.executor = executor
        self.system_prompt = system_prompt or build_system_prompt(registry)
        self.max_steps = max_steps
        self.memory = memory

    # ------------------------------------------------------------------
    def run(self, user_input: str, user_id: str | None = None,
            session_id: str = "default") -> MinimalResult:
        if self.memory is not None and user_id:
            self.memory.note(session_id, "user_id", user_id)

        context = self.memory.context_block(session_id) if self.memory else ""
        head = []
        if context:
            head.append(context)
        if user_id:
            head.append(f"用户ID: {user_id}")
        head.append(f"用户问题: {user_input}")
        user_content = "\n".join(head)

        messages: list[dict] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_content},
        ]
        result = MinimalResult(user_input=user_input, user_id=user_id,
                               session_id=session_id, answer="", status="error")
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
                    self._run_tool_call(call, messages, result, step, session_id)
                continue

            result.answer = (message.get("content") or "").strip()
            result.status = "completed"
            result.steps.append({"step": step, "type": "answer", "content": result.answer})
            break
        else:
            result.answer = "（达到最大步数仍未得到最终回答）"
            result.status = "max_steps"
            result.steps.append({"step": self.max_steps, "type": "answer",
                                 "content": result.answer})

        if self.memory is not None:
            self.memory.remember_turn(session_id, user_input, result.answer)
        return result

    # ------------------------------------------------------------------
    def _run_tool_call(self, call: dict, messages: list[dict],
                       result: MinimalResult, step: int, session_id: str) -> None:
        function = call.get("function") or {}
        name = function.get("name") or ""
        try:
            arguments = json.loads(function.get("arguments") or "{}")
        except json.JSONDecodeError:
            arguments = {}

        outcome = self.executor.execute(name, arguments)
        result.tool_calls.append(ToolCall(
            name=name, arguments=arguments, ok=outcome.ok,
            result=outcome.data if outcome.ok else None,
            error=outcome.error, latency_ms=outcome.latency_ms, attempts=outcome.attempts,
        ))
        result.steps.append({
            "step": step, "type": "tool", "tool": name, "arguments": arguments,
            "ok": outcome.ok, "latency_ms": round(outcome.latency_ms, 2),
            "attempts": outcome.attempts, "error": outcome.error,
        })

        if self.memory is not None and outcome.ok:
            self.memory.observe(session_id, name, outcome.data)

        # 工具结果以 role: tool 回灌给 LLM —— 这就是图里的 "Tool Result"
        messages.append({
            "role": "tool",
            "tool_call_id": call.get("id"),
            "content": json.dumps(outcome.to_dict(), ensure_ascii=False),
        })
