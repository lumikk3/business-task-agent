"""Task context / memory (DESIGN.md 5.6).

A TaskContext is the working memory of one agent task: intent, plan, slots
(order/product/risk), tool observations and the conversation history. Memory
is session-scoped so multi-turn inputs ("就是昨天买的那个") can resolve against
the previous turn's slots.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TaskContext:
    user_id: str
    session_id: str
    task_id: str
    user_input: str
    history: list[dict] = field(default_factory=list)   # previous turns
    intent: str = ""
    plan: list[str] = field(default_factory=list)
    slots: dict[str, Any] = field(default_factory=dict)  # order_id, product_id, risk_level...
    observations: dict[str, dict] = field(default_factory=dict)  # tool name -> result
    tool_sequence: list[str] = field(default_factory=list)
    step_index: int = 0
    risk_level: str = "LOW"

    def record_observation(self, tool: str, result: dict) -> None:
        self.observations[tool] = result
        self.tool_sequence.append(tool)

    def note(self, key: str, value: Any) -> None:
        self.slots[key] = value


class MemoryStore:
    """Session-scoped memory. New turns inherit slots + history."""

    def __init__(self) -> None:
        self._sessions: dict[str, TaskContext] = {}

    def start_turn(self, user_id: str, session_id: str, user_input: str) -> TaskContext:
        previous = self._sessions.get(session_id)
        history: list[dict] = []
        slots: dict[str, Any] = {}
        if previous is not None:
            history = previous.history + [{
                "user_input": previous.user_input,
                "intent": previous.intent,
                "response": previous.slots.get("final_response"),
                "slots": {k: v for k, v in previous.slots.items() if k != "final_response"},
            }]
            slots = dict(previous.slots)
            slots.pop("final_response", None)
        context = TaskContext(
            user_id=user_id,
            session_id=session_id,
            task_id=f"TASK-{uuid.uuid4().hex[:8].upper()}",
            user_input=user_input,
            history=history,
            slots=slots,
        )
        self._sessions[session_id] = context
        return context

    def get(self, session_id: str) -> TaskContext | None:
        return self._sessions.get(session_id)
