"""最小版 Agent（Step 3~6 / Step 9 / Step 11 / Step 13）。

核心循环就是设计里那一张图：

    User -> LLM -> Tool -> Tool Result -> LLM -> Answer

下一步调用什么工具**由 LLM 决定**，不是代码里写死的调用序列。

在此之上叠加了两层（都可在构造时关掉）：

* Step 11 权限门 —— 每个工具调用先过 PermissionManager：
  ``auto`` 直接执行 / ``confirm`` 挂起等用户确认 / ``human`` 转人工工单。
* Step 13 Trace —— 每次运行落盘 JSONL（node / tool / args / latency / retry / token）。

对比完整版 ``AgentRuntime``（Planner + 6 工具 + 权限 + 人工接管 + Trace），这里仍然是
刻意的最小实现。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from app.agent.permissions import PermissionPolicy
from app.tools.registry import ToolExecutor, ToolRegistry
from app.trace.tracer import Tracer

_PROMPT_HEADER = (
    "你是电商售后客服 Agent。你的目标是通过调用工具**完成**用户的业务任务,"
    "而不只是回答问题。\n\n可用工具:\n{tools}\n\n规则:\n{rules}"
)

_BASE_RULES = [
    "先调用工具拿真实数据再回答,不要编造订单号、物流或政策内容。",
    "用户消息里会给出用户ID,并可能带有 [会话上下文]。",
]

_LOGISTICS_RULE = "问物流:先用 query_order 找到订单,再用 query_logistics 查物流。"
_POLICY_RULE = ("问能不能退/售后政策:先用 query_order 拿商品与签收日期,"
                "再用 search_after_sales_policy 检索政策,结合签收天数给出结论。")

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
    if "query_logistics" in names:
        rules.append(_LOGISTICS_RULE)
    if "search_after_sales_policy" in names:
        rules.append(_POLICY_RULE)
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
    status: str                       # completed | awaiting_confirmation | cancelled
    #                                 # escalated | max_steps | error
    tool_calls: list[ToolCall] = field(default_factory=list)
    steps: list[dict] = field(default_factory=list)
    iterations: int = 0
    tokens: int = 0
    trace_id: str = ""
    pending: dict | None = None

    def to_dict(self) -> dict:
        return {
            "user_input": self.user_input,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "answer": self.answer,
            "status": self.status,
            "iterations": self.iterations,
            "tokens": self.tokens,
            "trace_id": self.trace_id,
            "pending": self.pending,
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
                 memory=None, permission_policy: PermissionPolicy | None = None,
                 trace_dir: Path | str | None = None, parallel_tools: bool = False):
        self.client = client
        self.registry = registry
        self.executor = executor
        self.system_prompt = system_prompt or build_system_prompt(registry)
        self.max_steps = max_steps
        self.memory = memory
        self.permission_policy = (permission_policy if permission_policy is not None
                                  else PermissionPolicy())
        self.trace_dir = Path(trace_dir) if trace_dir else None
        # Step 19：同一轮里多个「自动放行」的工具调用并发执行（否则串行）
        self.parallel_tools = parallel_tools
        self._pending: dict[str, dict] = {}

    # ------------------------------------------------------------------ run
    def run(self, user_input: str, user_id: str | None = None,
            session_id: str = "default") -> MinimalResult:
        if self.memory is not None and user_id:
            self.memory.note(session_id, "user_id", user_id)

        context = self.memory.context_block(session_id) if self.memory else ""
        head = [block for block in (context, f"用户ID: {user_id}" if user_id else "") if block]
        head.append(f"用户问题: {user_input}")
        user_content = "\n".join(head)

        tracer = Tracer()
        tracer.record("user_input", input=user_content)
        messages: list[dict] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_content},
        ]
        result = MinimalResult(user_input=user_input, user_id=user_id,
                               session_id=session_id, answer="", status="error",
                               trace_id=tracer.trace_id)
        result.steps.append({"step": 0, "type": "user", "content": user_content})
        return self._loop(messages, result, session_id, tracer, start_step=1)

    def resume(self, session_id: str, confirm: bool) -> MinimalResult:
        """处理被权限门挂起的确认（Step 11）。"""
        pending = self._pending.pop(session_id, None)
        if pending is None:
            raise ValueError(f"no pending decision in session: {session_id}")
        messages, result = pending["messages"], pending["result"]
        tracer, step = pending["tracer"], pending["step"]
        call_id, remaining = pending["call_id"], pending["remaining"]
        if not confirm:
            answer = "好的,已取消该操作。"
            result.answer, result.status, result.pending = answer, "cancelled", None
            result.steps.append({"step": step, "type": "confirm", "content": "user cancelled"})
            messages.append({"role": "tool", "tool_call_id": call_id,
                             "content": json.dumps({"cancelled": True}, ensure_ascii=False)})
            self._append_skipped(messages, remaining)
            tracer.record("confirm", input={"confirm": False}, output=answer)
            return self._finish(result, tracer, session_id)
        tracer.record("confirm", input={"confirm": True, "tool": pending["tool"]})
        result.pending = None
        self._execute_tool(pending["tool"], pending["arguments"], messages, result,
                           step, session_id, tracer, call_id=call_id)
        self._append_skipped(messages, remaining)
        return self._loop(messages, result, session_id, tracer, start_step=step + 1)

    @staticmethod
    def _append_skipped(messages: list[dict], remaining: list[dict]) -> None:
        """同一批里被跳过的工具调用要补齐 role:tool,否则下一轮 LLM 请求缺结果会报错。"""
        for item in remaining:
            messages.append({"role": "tool", "tool_call_id": item.get("id"),
                             "content": json.dumps({"skipped": True}, ensure_ascii=False)})

    # ----------------------------------------------------------------- loop
    def _loop(self, messages: list[dict], result: MinimalResult, session_id: str,
              tracer: Tracer, start_step: int) -> MinimalResult:
        schemas = self.registry.schemas()
        step = start_step - 1
        while step - start_step + 1 < self.max_steps:
            step += 1
            result.iterations = step
            response = self.client.chat(messages, tools=schemas)
            tokens = int((response.get("usage") or {}).get("total_tokens", 0))
            result.tokens += tokens
            tracer.record("llm", input={"model": getattr(self.client.config, "model", "")},
                          tokens=tokens)

            message = response["choices"][0]["message"]
            tool_calls = message.get("tool_calls") or []

            if tool_calls:
                messages.append({"role": "assistant",
                                 "content": message.get("content") or "",
                                 "tool_calls": tool_calls})
                if (self.parallel_tools and len(tool_calls) > 1
                        and self._batch_is_auto(tool_calls)):
                    self._execute_batch(tool_calls, messages, result, session_id, step, tracer)
                    continue
                for index, call in enumerate(tool_calls):
                    remaining = tool_calls[index + 1:]
                    if self._handle_call(call, remaining, messages, result, session_id,
                                         step, tracer) == "stop":
                        return self._finish(result, tracer, session_id)
                continue

            result.answer = (message.get("content") or "").strip()
            result.status = "completed"
            result.steps.append({"step": step, "type": "answer", "content": result.answer})
            tracer.record("final", output=result.answer)
            return self._finish(result, tracer, session_id)

        result.answer = "（达到最大步数仍未得到最终回答）"
        result.status = "max_steps"
        result.steps.append({"step": step, "type": "answer", "content": result.answer})
        tracer.record("final", output=result.answer)
        return self._finish(result, tracer, session_id)

    # ---------------------------------------------------------- call + gate
    def _handle_call(self, call: dict, remaining: list[dict], messages: list[dict],
                     result: MinimalResult, session_id: str, step: int,
                     tracer: Tracer) -> str:
        """返回 'continue' 或 'stop'。"""
        function = call.get("function") or {}
        name = function.get("name") or ""
        call_id = call.get("id")
        try:
            arguments = json.loads(function.get("arguments") or "{}")
        except json.JSONDecodeError:
            arguments = {}

        # 未知/幻觉工具名：不拦截,交给 executor 返回 not_found 再回灌给 LLM。
        if name not in self.registry.names():
            self._execute_tool(name, arguments, messages, result, step, session_id,
                               tracer, call_id=call_id)
            return "continue"

        risk_level = self.registry.risk_level(name)
        decision = self.permission_policy.evaluate(name, arguments, risk_level)
        tracer.record("permission",
                      input={"tool": name, "arguments": arguments, "risk": risk_level.value},
                      output={"mode": decision.mode, "reason": decision.reason})
        result.steps.append({"step": step, "type": "permission", "tool": name,
                             "mode": decision.mode, "reason": decision.reason,
                             "risk_level": risk_level.value})

        if decision.mode == "auto":
            self._execute_tool(name, arguments, messages, result, step, session_id,
                               tracer, call_id=call_id)
            return "continue"

        if decision.mode == "confirm":
            result.status = "awaiting_confirmation"
            result.pending = {"tool": name, "arguments": arguments, "reason": decision.reason}
            result.answer = (f"操作「{name}」需要您确认（{decision.reason}）。"
                             f"请回复确认继续,或取消。")
            result.steps.append({"step": step, "type": "answer", "content": result.answer})
            self._pending[session_id] = {"messages": messages, "result": result,
                                         "tracer": tracer, "tool": name,
                                         "arguments": arguments, "step": step,
                                         "call_id": call_id, "remaining": remaining}
            return "stop"

        # decision.mode == "human" —— 转人工（有 create_human_ticket 就落工单）
        reason = f"操作 {name} {decision.reason}"
        if "create_human_ticket" in self.registry.names():
            self._execute_tool("create_human_ticket",
                               {"user_id": result.user_id, "reason": reason},
                               messages, result, step, session_id, tracer, call_id=call_id)
            ticket = next((c.result for c in reversed(result.tool_calls)
                           if c.name == "create_human_ticket" and c.result), None)
            ticket_id = ((ticket or {}).get("ticket") or {}).get("ticket_id", "未知")
            result.answer = f"该操作需要人工审批,已创建工单{ticket_id}。原因:{reason}。"
        else:
            result.answer = f"该操作需要人工审批,已转人工处理。原因:{reason}。"
        result.status = "escalated"
        result.pending = None
        result.steps.append({"step": step, "type": "answer", "content": result.answer})
        self._append_skipped(messages, remaining)
        tracer.record("final", output=result.answer)
        return "stop"

    def _execute_tool(self, name: str, arguments: dict, messages: list[dict],
                      result: MinimalResult, step: int, session_id: str,
                      tracer: Tracer, call_id: str | None = None) -> None:
        outcome = self.executor.execute(name, arguments)
        self._record_outcome(name, arguments, outcome, result, step, session_id, tracer)
        messages.append({"role": "tool", "tool_call_id": call_id,
                         "content": json.dumps(outcome.to_dict(), ensure_ascii=False)})

    def _record_outcome(self, name: str, arguments: dict, outcome, result: MinimalResult,
                        step: int, session_id: str, tracer: Tracer) -> None:
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
        tracer.record("tool", tool=name, arguments=arguments,
                      output=outcome.data if outcome.ok else None,
                      error=outcome.error, latency_ms=outcome.latency_ms,
                      retry_count=max(outcome.attempts - 1, 0))
        if self.memory is not None and outcome.ok:
            self.memory.observe(session_id, name, outcome.data)

    # ---- Step 19: 同一轮多工具并发 --------------------------------------
    def _batch_is_auto(self, calls: list[dict]) -> bool:
        for call in calls:
            function = call.get("function") or {}
            name = function.get("name") or ""
            if name not in self.registry.names():
                return False
            try:
                arguments = json.loads(function.get("arguments") or "{}")
            except json.JSONDecodeError:
                arguments = {}
            decision = self.permission_policy.evaluate(
                name, arguments, self.registry.risk_level(name))
            if decision.mode != "auto":          # 有 confirm/human 就退回串行处理
                return False
        return True

    def _execute_batch(self, calls: list[dict], messages: list[dict],
                       result: MinimalResult, session_id: str, step: int,
                       tracer: Tracer) -> None:
        """一轮里多个自动放行的调用**并发**执行,结果按原顺序回灌。"""
        from app.agent.async_tools import gather

        parsed: list[tuple[str | None, str, dict]] = []
        for call in calls:
            function = call.get("function") or {}
            name = function.get("name") or ""
            try:
                arguments = json.loads(function.get("arguments") or "{}")
            except json.JSONDecodeError:
                arguments = {}
            risk_level = self.registry.risk_level(name)
            decision = self.permission_policy.evaluate(name, arguments, risk_level)
            tracer.record("permission",
                          input={"tool": name, "arguments": arguments, "risk": risk_level.value},
                          output={"mode": decision.mode, "reason": decision.reason})
            result.steps.append({"step": step, "type": "permission", "tool": name,
                                 "mode": decision.mode, "reason": decision.reason,
                                 "risk_level": risk_level.value})
            parsed.append((call.get("id"), name, arguments))

        outcomes = gather(self.executor, [(name, args) for _, name, args in parsed])
        for (call_id, name, arguments), outcome in zip(parsed, outcomes):
            self._record_outcome(name, arguments, outcome, result, step, session_id, tracer)
            messages.append({"role": "tool", "tool_call_id": call_id,
                             "content": json.dumps(outcome.to_dict(), ensure_ascii=False)})

    # ---------------------------------------------------------------- finish
    def _finish(self, result: MinimalResult, tracer: Tracer, session_id: str) -> MinimalResult:
        if self.trace_dir:
            tracer.save(self.trace_dir / "agent_traces.jsonl")
        if self.memory is not None and result.status in ("completed", "max_steps", "escalated"):
            self.memory.remember_turn(session_id, result.user_input, result.answer)
        return result
