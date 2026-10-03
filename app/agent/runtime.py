"""Agent Runtime — the task execution engine (DESIGN.md 2.1 / 5.1 / 5.11).

Drives the agent loop: intent -> plan -> (decide -> permission -> tool ->
observe)* -> final answer / human handoff, recording a full trace. The brain
is pluggable: RuleBasedBrain for the deterministic demo, OpenAIBrain for
LLM-driven decisions — both implement `decide(ctx) -> Decision`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.agent.brain import Decision
from app.agent.intents import Intent, RuleBasedIntentRecognizer
from app.agent.permissions import PermissionDecision, PermissionPolicy
from app.agent.planner import build_plan
from app.memory.context import MemoryStore, TaskContext
from app.tools.registry import RiskLevel, ToolRegistry, ToolExecutor
from app.trace.tracer import Tracer


@dataclass
class TaskResult:
    task_id: str
    trace_id: str
    session_id: str
    user_id: str
    status: str            # completed | escalated | awaiting_confirmation | cancelled | failed
    intent: str
    plan: list[str]
    final_response: str
    tool_calls: list[dict] = field(default_factory=list)
    risk_level: str = "LOW"
    pending: dict | None = None
    tokens: int = 0
    latency_ms: float = 0.0

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "trace_id": self.trace_id,
            "session_id": self.session_id,
            "user_id": self.user_id,
            "status": self.status,
            "intent": self.intent,
            "plan": self.plan,
            "final_response": self.final_response,
            "tool_calls": self.tool_calls,
            "risk_level": self.risk_level,
            "pending": self.pending,
            "tokens": self.tokens,
            "latency_ms": self.latency_ms,
        }


class AgentRuntime:
    def __init__(self, brain, registry: ToolRegistry, executor: ToolExecutor,
                 memory: MemoryStore | None = None,
                 permission_policy: PermissionPolicy | None = None,
                 max_steps: int = 8, trace_dir: Path | str | None = None):
        self.brain = brain
        self.registry = registry
        self.executor = executor
        self.memory = memory or MemoryStore()
        self.permission_policy = permission_policy or PermissionPolicy()
        self.recognizer = RuleBasedIntentRecognizer()
        self.max_steps = max_steps
        self.trace_dir = Path(trace_dir) if trace_dir else None

    # ------------------------------------------------------------------
    def run(self, user_input: str, user_id: str = "U10001",
            session_id: str = "default") -> TaskResult:
        ctx = self.memory.start_turn(user_id, session_id, user_input)
        tracer = Tracer()
        ctx.tracer = tracer
        tracer.record("user_input", input=user_input)
        ctx.intent = self.recognizer.recognize(user_input).value
        tracer.record("intent", input=user_input, output=ctx.intent)
        ctx.plan = build_plan(Intent(ctx.intent))
        tracer.record("plan", output=ctx.plan)
        return self._loop(ctx, tracer)

    def resume(self, session_id: str, confirm: bool) -> TaskResult:
        """Continue a task paused at a confirmation gate (human-in-the-loop)."""
        ctx = self.memory.get(session_id)
        tracer = getattr(ctx, "tracer", None) or Tracer()
        if ctx is None:
            raise ValueError(f"unknown session: {session_id}")
        pending = ctx.slots.pop("pending_decision", None)
        if pending is None:
            raise ValueError(f"no pending decision in session: {session_id}")
        if not confirm:
            response = "好的,已取消该操作。"
            ctx.note("final_response", response)
            tracer.record("confirm", input={"confirm": False}, output=response)
            return self._result(ctx, tracer, "cancelled", response)
        tracer.record("confirm", input={"confirm": True, **pending})
        outcome = self._handle_tool(ctx, tracer, Decision(
            "tool", tool=pending["tool"], arguments=pending["arguments"],
            reason=pending.get("reason", "")), confirmed=True)
        if outcome["status"] != "continue":
            return self._result(ctx, tracer, outcome["status"], outcome["response"])
        return self._loop(ctx, tracer)

    # ------------------------------------------------------------------
    def _loop(self, ctx: TaskContext, tracer: Tracer) -> TaskResult:
        response, status = "", "failed"
        for _ in range(self.max_steps):
            decision = self.brain.decide(ctx)
            tracer.record("decision", input={"intent": ctx.intent},
                          output={"kind": decision.kind, "tool": decision.tool,
                                  "reason": decision.reason})
            if decision.kind == "tool":
                outcome = self._handle_tool(ctx, tracer, decision)
                if outcome["status"] == "continue":
                    continue
                return self._result(ctx, tracer, outcome["status"], outcome["response"])
            if decision.kind == "answer":
                response, status = decision.answer or "", "completed"
                break
            outcome = self._escalate(ctx, tracer, decision)
            return self._result(ctx, tracer, outcome["status"], outcome["response"])
        else:
            response = "任务执行步骤过多,已转人工处理。"
            outcome = self._escalate(ctx, tracer, Decision(
                "escalate", reason="超过最大执行步数", recommended_action="人工接手任务"))
            return self._result(ctx, tracer, outcome["status"], outcome["response"])
        ctx.note("final_response", response)
        tracer.record("final", output=response)
        return self._result(ctx, tracer, status, response)

    def _handle_tool(self, ctx: TaskContext, tracer: Tracer,
                     decision: Decision, confirmed: bool = False) -> dict:
        tool, arguments = decision.tool, decision.arguments or {}
        risk_level = self.registry.risk_level(tool)
        if confirmed:
            # user already answered the confirmation gate — do not re-ask
            permission = PermissionDecision("auto", "用户已确认执行", risk_level)
        else:
            permission = self.permission_policy.evaluate(tool, arguments, risk_level)
        tracer.record("permission", input={"tool": tool, "arguments": arguments},
                      output={"mode": permission.mode, "reason": permission.reason,
                              "risk_level": risk_level.value})
        ctx.note("risk_level", max(str(ctx.risk_level), risk_level.value,
                                   key=lambda r: ["LOW", "MEDIUM", "HIGH", "CRITICAL"].index(r)))
        if permission.mode == "human":
            return self._escalate(ctx, tracer, Decision(
                "escalate", reason=permission.reason,
                recommended_action=f"人工审批操作:{tool} {arguments}"))
        if permission.mode == "confirm":
            response = (f"操作「{tool}」需要您确认({permission.reason})。"
                        f"请回复确认继续,或取消。")
            ctx.note("pending_decision", {"tool": tool, "arguments": arguments,
                                          "reason": decision.reason})
            ctx.note("final_response", response)
            tracer.record("final", output=response)
            return {"status": "awaiting_confirmation", "response": response,
                    "pending": {"tool": tool, "arguments": arguments,
                                "reason": permission.reason}}

        result = self.executor.execute(tool, arguments)
        tracer.record("tool", tool=tool, arguments=arguments,
                      output=result.data if result.ok else None,
                      error=result.error, latency_ms=result.latency_ms,
                      retry_count=max(result.attempts - 1, 0))
        if not result.ok:
            # Fallback per DESIGN.md 5.9: exhausted retries -> human handling
            return self._escalate(ctx, tracer, Decision(
                "escalate",
                reason=f"工具{tool}连续失败({result.error_type.value if result.error_type else 'error'}):{result.error}",
                recommended_action="人工跟进该任务,并排查工具故障"))
        ctx.record_observation(tool, result.to_dict())
        return {"status": "continue"}

    def _escalate(self, ctx: TaskContext, tracer: Tracer,
                  decision: Decision) -> dict:
        arguments = {
            "user_id": ctx.user_id,
            "reason": decision.reason,
            "order_id": ctx.slots.get("order_id"),
            "recommended_action": decision.recommended_action,
        }
        result = self.executor.execute("create_human_ticket", arguments)
        tracer.record("tool", tool="create_human_ticket", arguments=arguments,
                      output=result.data if result.ok else None, error=result.error,
                      latency_ms=result.latency_ms,
                      retry_count=max(result.attempts - 1, 0))
        ticket_id = "未知"
        if result.ok:
            ticket_id = (result.data or {}).get("ticket", {}).get("ticket_id", "未知")
            ctx.record_observation("create_human_ticket", result.to_dict())
        response = (f"您的情况需要人工处理,已创建工单{ticket_id}。"
                    f"原因:{decision.reason}。客服将在48小时内与您联系。")
        ctx.note("final_response", response)
        tracer.record("final", output=response)
        return {"status": "escalated", "response": response}

    def _result(self, ctx: TaskContext, tracer: Tracer, status: str,
                response: str) -> TaskResult:
        if self.trace_dir:
            tracer.save(self.trace_dir / "agent_traces.jsonl")
        return TaskResult(
            task_id=ctx.task_id,
            trace_id=tracer.trace_id,
            session_id=ctx.session_id,
            user_id=ctx.user_id,
            status=status,
            intent=ctx.intent,
            plan=ctx.plan,
            final_response=response,
            tool_calls=[{"tool": name, "ok": obs.get("ok")}
                        for name, obs in ctx.observations.items()],
            risk_level=str(ctx.risk_level),
            pending=ctx.slots.get("pending_decision"),
            tokens=int(ctx.slots.get("tokens", 0)),
            latency_ms=tracer.to_dict()["total_latency_ms"],
        )
