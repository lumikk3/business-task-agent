"""会话级记忆（DESIGN.md 5.6 / Step 9）—— 让 Agent 记住任务状态。

第一版就用 Python dict（进程内），把每一轮的工具观测抽成任务槽位，供下一轮复用：

    第一轮: 我的耳机坏了。            -> 记住 当前用户 / 当前订单 / 当前商品
    第二轮: 就是昨天那个订单到哪了?    -> 直接复用 当前订单,不必重问

接口刻意保持很小，之后把 ``_sessions`` 换成 Redis 即可，调用方不用改。
"""
from __future__ import annotations

from dataclasses import dataclass, field

_MAX_HISTORY = 3


@dataclass
class SessionMemory:
    """进程内的会话记忆（可替换为 Redis）。"""

    _sessions: dict[str, dict] = field(default_factory=dict)

    # ---- session access ------------------------------------------------
    def session(self, session_id: str) -> dict:
        return self._sessions.setdefault(session_id, {"history": [], "slots": {}, "seen": {}})

    def slots(self, session_id: str) -> dict:
        return self.session(session_id)["slots"]

    def history(self, session_id: str) -> list[dict]:
        return self.session(session_id)["history"]

    # ---- write ---------------------------------------------------------
    def observe(self, session_id: str, tool: str, data: dict | None) -> None:
        """从工具结果里抽取任务槽位（order_id / product / refund...）。"""
        if not isinstance(data, dict):
            return
        slots = self.slots(session_id)
        if tool == "query_order":
            orders = data.get("orders") or []
            if orders:
                # 订单按 created_at DESC 返回,取最近一张作为「当前订单」
                current = orders[0]
                slots["order_id"] = current.get("order_id")
                slots["product"] = current.get("product")
                slots["category"] = current.get("category")
        elif tool == "query_logistics":
            if data.get("found"):
                slots["logistics_status"] = data.get("status")
                slots["order_id"] = data.get("order_id") or slots.get("order_id")
        elif tool == "create_return_request":
            request = (data or {}).get("return_request") or {}
            if request:
                slots["return_request_id"] = request.get("request_id")

    def remember_turn(self, session_id: str, user_input: str,
                      answer: str, intent: str | None = None) -> None:
        history = self.history(session_id)
        history.append({"user": user_input, "answer": answer, "intent": intent})
        del history[:-_MAX_HISTORY]

    def note(self, session_id: str, key: str, value) -> None:
        self.slots(session_id)[key] = value

    # ---- read ----------------------------------------------------------
    def context_block(self, session_id: str) -> str:
        """渲染成一小段文本,注入到下一轮的用户消息里。"""
        slots = self.slots(session_id)
        lines: list[str] = []
        if slots.get("user_id"):
            lines.append(f"当前用户: {slots['user_id']}")
        if slots.get("order_id"):
            product = f"({slots['product']})" if slots.get("product") else ""
            lines.append(f"当前订单: {slots['order_id']}{product}")
        if slots.get("logistics_status"):
            lines.append(f"最近查到的物流状态: {slots['logistics_status']}")
        if slots.get("return_request_id"):
            lines.append(f"已创建退货申请: {slots['return_request_id']}")
        if not lines:
            return ""
        return "[会话上下文]\n" + "\n".join(lines)
