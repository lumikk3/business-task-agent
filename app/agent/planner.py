"""Task planning (DESIGN.md 5.3).

The planner emits the execution steps for the recognized intent. These are
not decorative text: the runtime executes them through the agent loop, and the
trace records which step produced which tool call.
"""
from __future__ import annotations

from app.agent.intents import Intent

PLAN_TEMPLATES: dict[Intent, list[str]] = {
    Intent.ORDER_QUERY: ["查询订单", "返回订单与发货信息"],
    Intent.LOGISTICS_QUERY: ["查询订单", "查询物流", "返回物流状态"],
    Intent.AFTERSALE_POLICY: ["查询订单", "检索售后政策", "结合订单状态判断", "返回政策结论"],
    Intent.RETURN_REQUEST: ["查询订单", "查询商品", "检索售后政策", "判断退货资格",
                            "创建退货申请或转人工", "返回结果"],
    Intent.REFUND_QUERY: ["查询订单", "查询退货与退款状态", "返回退款信息"],
    Intent.HUMAN_SERVICE: ["创建人工工单", "通知用户"],
    Intent.UNKNOWN: ["澄清用户诉求"],
}


def build_plan(intent: Intent) -> list[str]:
    return list(PLAN_TEMPLATES.get(intent, PLAN_TEMPLATES[Intent.UNKNOWN]))
