"""The six V1 business tools (DESIGN.md 5.4) backed by the mock Store.

Tools are pure functions of their arguments over the data layer, so swapping
the mock Store for real Order/Logistics/AfterSale services (or MCP servers in
V3) does not change the agent core.
"""
from __future__ import annotations

from app.store import Store
from app.tools.registry import RiskLevel, ToolRegistry, ToolSpec


class BusinessTools:
    def __init__(self, store: Store, policy_search=None):
        self.store = store
        # policy_search: callable(query) -> list[dict], wired to the RAG module
        self.policy_search = policy_search

    # ---- tools ---------------------------------------------------------
    def query_order(self, user_id: str, order_id: str | None = None) -> dict:
        orders = self.store.find_orders(user_id, order_id)
        if not orders:
            return {"found": False, "orders": []}
        enriched = []
        for order in orders:
            items = self.store.get_order_items(order["order_id"])
            enriched.append({
                **order,
                "product": items[0]["product_name"] if items else None,
                "category": items[0]["category"] if items else None,
                "after_sale_requests": self.store.get_after_sale(order["order_id"]),
                "refunds": self.store.get_refunds(order["order_id"]),
            })
        return {"found": True, "orders": enriched}

    def query_logistics(self, order_id: str) -> dict:
        info = self.store.get_logistics(order_id)
        if not info:
            return {"found": False, "order_id": order_id}
        return {"found": True, **info}

    def search_after_sales_policy(self, query: str) -> dict:
        if self.policy_search is None:
            return {"found": False, "policies": []}
        hits = self.policy_search(query)
        return {"found": bool(hits), "query": query, "policies": hits}

    def create_return_request(self, order_id: str, reason: str) -> dict:
        order = self.store.get_order(order_id)
        if order is None:
            return {"success": False, "error": f"order not found: {order_id}"}
        request = self.store.create_return_request(order_id, reason)
        return {"success": True, "return_request": request}

    def create_refund_request(self, order_id: str, amount: float) -> dict:
        order = self.store.get_order(order_id)
        if order is None:
            return {"success": False, "error": f"order not found: {order_id}"}
        if amount <= 0 or amount > order["total"]:
            return {"success": False,
                    "error": f"invalid refund amount {amount}, order total {order['total']}"}
        refund = self.store.create_refund_request(order_id, float(amount))
        return {"success": True, "refund_request": refund}

    def create_human_ticket(self, user_id: str, reason: str,
                            order_id: str | None = None,
                            recommended_action: str | None = None) -> dict:
        ticket = self.store.create_ticket(user_id, order_id, reason, recommended_action)
        return {"success": True, "ticket": ticket}


def build_registry(tools: BusinessTools) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(ToolSpec(
        name="query_order",
        description="查询用户订单,包含商品、物流概要、售后与退款记录。",
        parameters={
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "用户ID,例如 U10001"},
                "order_id": {"type": "string", "description": "可选,指定订单ID"},
            },
            "required": ["user_id"],
        },
        handler=tools.query_order,
        risk_level=RiskLevel.LOW,
    ))
    registry.register(ToolSpec(
        name="query_logistics",
        description="查询订单的物流状态、承运商与最新轨迹。",
        parameters={
            "type": "object",
            "properties": {"order_id": {"type": "string", "description": "订单ID"}},
            "required": ["order_id"],
        },
        handler=tools.query_logistics,
        risk_level=RiskLevel.LOW,
    ))
    registry.register(ToolSpec(
        name="search_after_sales_policy",
        description="RAG检索售后政策知识库(退货、退款、运费、人工审核规则)。",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string", "description": "自然语言问题"}},
            "required": ["query"],
        },
        handler=tools.search_after_sales_policy,
        risk_level=RiskLevel.LOW,
    ))
    registry.register(ToolSpec(
        name="create_return_request",
        description="为订单创建退货申请。",
        parameters={
            "type": "object",
            "properties": {
                "order_id": {"type": "string", "description": "订单ID"},
                "reason": {"type": "string", "description": "退货原因"},
            },
            "required": ["order_id", "reason"],
        },
        handler=tools.create_return_request,
        risk_level=RiskLevel.MEDIUM,
    ))
    registry.register(ToolSpec(
        name="create_refund_request",
        description="为订单创建退款申请,金额受退款权限分级约束。",
        parameters={
            "type": "object",
            "properties": {
                "order_id": {"type": "string", "description": "订单ID"},
                "amount": {"type": "number", "description": "退款金额(元)"},
            },
            "required": ["order_id", "amount"],
        },
        handler=tools.create_refund_request,
        risk_level=RiskLevel.HIGH,
    ))
    registry.register(ToolSpec(
        name="create_human_ticket",
        description="创建人工工单,将任务转交人工客服处理。",
        parameters={
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "用户ID"},
                "reason": {"type": "string", "description": "转人工原因"},
                "order_id": {"type": "string", "description": "可选,关联订单ID"},
                "recommended_action": {"type": "string", "description": "建议处理方式"},
            },
            "required": ["user_id", "reason"],
        },
        handler=tools.create_human_ticket,
        risk_level=RiskLevel.LOW,
    ))
    return registry


# ---- 最小版（Step 4）：第一版只暴露三个工具 -----------------------------
# 对应 DESIGN/课程里的第四阶段：先让三个问题跑通，不引入 Planner 和权限门。
MINIMAL_TOOLS = ("query_order", "query_logistics", "search_after_sales_policy")


def build_minimal_registry(tools: BusinessTools,
                           names: tuple[str, ...] = MINIMAL_TOOLS) -> ToolRegistry:
    """只注册 ``names`` 里列出的工具，供最小版 Agent 使用。"""
    full = build_registry(tools)
    registry = ToolRegistry()
    for name in names:
        registry.register(full.get(name))
    return registry
