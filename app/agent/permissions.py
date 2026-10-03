"""Permission control (DESIGN.md 5.8).

Every tool carries a risk level; sensitive money-moving operations add
amount-based gates:

    refund < 100        -> auto
    refund 100 .. 500   -> user confirmation (human-in-the-loop)
    refund > 500        -> human approval

Thresholds are constructor-configurable so they are business config, not
hardcoded law.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.tools.registry import RiskLevel


@dataclass
class PermissionDecision:
    mode: str            # "auto" | "confirm" | "human"
    reason: str
    risk_level: RiskLevel

    @property
    def allowed(self) -> bool:
        return self.mode == "auto"


class PermissionPolicy:
    def __init__(self, auto_max: float = 100.0, confirm_max: float = 500.0):
        self.auto_max = auto_max
        self.confirm_max = confirm_max

    def evaluate(self, tool_name: str, arguments: dict,
                 risk_level: RiskLevel) -> PermissionDecision:
        amount = _extract_amount(tool_name, arguments)
        if amount is not None:
            if amount < self.auto_max:
                return PermissionDecision("auto", f"金额{amount}元低于自动执行阈值{self.auto_max}元",
                                          risk_level)
            if amount <= self.confirm_max:
                return PermissionDecision("confirm",
                                          f"金额{amount}元需用户二次确认", risk_level)
            return PermissionDecision("human",
                                      f"金额{amount}元超过{self.confirm_max}元,需人工审批", risk_level)
        if risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL):
            return PermissionDecision("human", f"操作风险等级{risk_level.value},需人工审批", risk_level)
        return PermissionDecision("auto", f"操作风险等级{risk_level.value},允许自动执行", risk_level)


def _extract_amount(tool_name: str, arguments: dict) -> float | None:
    if tool_name == "create_refund_request":
        try:
            return float(arguments.get("amount"))
        except (TypeError, ValueError):
            return None
    return None
