"""Agent decision brain.

`RuleBasedBrain.decide()` is a pure function of the TaskContext: it looks at
the recognized intent, the plan, and every observation gathered so far, and
returns the next action (call a tool / answer / escalate). The runtime drives
it in a loop, so each tool result genuinely feeds the next decision — the same
contract an LLM brain must implement (see llm_brain.py).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from app.agent.intents import Intent
from app.memory.context import TaskContext
from app.store import demo_today

QUALITY_KEYWORDS = ("坏", "故障", "损坏", "失灵", "质量", "问题", "碎", "不能用", "坏了")
PRODUCT_KEYWORDS = {"耳机": ("耳机",), "键盘": ("键盘",), "咖啡": ("咖啡",)}


@dataclass
class Decision:
    kind: str                    # "tool" | "answer" | "escalate"
    tool: str | None = None
    arguments: dict | None = None
    answer: str | None = None
    reason: str = ""
    recommended_action: str | None = None


def _quality_claim(text: str) -> bool:
    return any(word in text for word in QUALITY_KEYWORDS)


def _order_keywords(text: str) -> list[str]:
    return [word for word, aliases in PRODUCT_KEYWORDS.items()
            if any(alias in text for alias in aliases)]


class RuleBasedBrain:
    def __init__(self, quality_window_days: int = 15, no_reason_window_days: int = 7):
        # 售后时效是业务配置：质量问题默认 15 天,无理由 7 天。把它做成可注入参数,
        # 方便做回归实验（例如把 quality_window 调小就能复现一批退货资格误判）。
        self.quality_window_days = quality_window_days
        self.no_reason_window_days = no_reason_window_days

    def decide(self, ctx: TaskContext) -> Decision:
        handler = {
            Intent.ORDER_QUERY: self._decide_order_query,
            Intent.LOGISTICS_QUERY: self._decide_logistics,
            Intent.AFTERSALE_POLICY: self._decide_policy,
            Intent.RETURN_REQUEST: self._decide_return,
            Intent.REFUND_QUERY: self._decide_refund,
            Intent.HUMAN_SERVICE: self._decide_human,
            Intent.UNKNOWN: self._decide_unknown,
        }.get(ctx.intent, self._decide_unknown)
        return handler(ctx)

    # ---- per-intent policies ------------------------------------------
    def _decide_order_query(self, ctx: TaskContext) -> Decision:
        if "query_order" not in ctx.observations:
            return Decision("tool", tool="query_order", arguments={"user_id": ctx.user_id},
                            reason="查询用户订单")
        data = ctx.observations["query_order"]["data"]
        order = self._select_order(ctx, (data or {}).get("orders", []), prefer="paid")
        if order is None:
            return Decision("answer", answer="暂时没有查询到您的订单,请确认订单信息后重试。")
        self._remember(ctx, order)
        return Decision("answer", answer=self._render_order(order))

    def _decide_logistics(self, ctx: TaskContext) -> Decision:
        if "query_order" not in ctx.observations:
            return Decision("tool", tool="query_order", arguments={"user_id": ctx.user_id},
                            reason="先查询订单以确定目标订单")
        data = ctx.observations["query_order"]["data"]
        order = self._select_order(ctx, (data or {}).get("orders", []), prefer="shipped")
        if order is None:
            return Decision("answer", answer="没有查询到相关订单,无法查看物流。")
        self._remember(ctx, order)
        if "query_logistics" not in ctx.observations:
            return Decision("tool", tool="query_logistics",
                            arguments={"order_id": order["order_id"]},
                            reason=f"查询订单{order['order_id']}的物流")
        logistics = (ctx.observations["query_logistics"]["data"] or {})
        return Decision("answer", answer=self._render_logistics(order, logistics))

    def _decide_policy(self, ctx: TaskContext) -> Decision:
        if "query_order" not in ctx.observations:
            return Decision("tool", tool="query_order", arguments={"user_id": ctx.user_id},
                            reason="获取商品信息以匹配售后政策")
        if "search_after_sales_policy" not in ctx.observations:
            return Decision("tool", tool="search_after_sales_policy",
                            arguments={"query": ctx.user_input},
                            reason="RAG检索售后政策")
        data = ctx.observations["query_order"]["data"]
        order = self._select_order(ctx, (data or {}).get("orders", []))
        policies = (ctx.observations["search_after_sales_policy"]["data"] or {}).get("policies", [])
        return Decision("answer", answer=self._render_policy(ctx, order, policies))

    def _decide_return(self, ctx: TaskContext) -> Decision:
        if "query_order" not in ctx.observations:
            return Decision("tool", tool="query_order", arguments={"user_id": ctx.user_id},
                            reason="查询订单")
        if "search_after_sales_policy" not in ctx.observations:
            return Decision("tool", tool="search_after_sales_policy",
                            arguments={"query": ctx.user_input},
                            reason="检索售后政策判断退货资格")
        data = ctx.observations["query_order"]["data"]
        order = self._select_order(ctx, (data or {}).get("orders", []))
        if order is None:
            return Decision("answer", answer="没有找到需要退货的订单,请提供订单信息。")
        self._remember(ctx, order)

        verdict, reason, days = self._evaluate_return(ctx, order)
        amount = float(order.get("total") or 0)

        if "create_return_request" in ctx.observations:
            result = ctx.observations["create_return_request"]["data"] or {}
            return Decision("answer", answer=self._render_return_created(order, result))

        if verdict == "eligible" and amount < 1000:
            return Decision("tool", tool="create_return_request",
                            arguments={"order_id": order["order_id"],
                                       "reason": ctx.user_input},
                            reason=f"符合退货条件({reason})")
        if verdict == "not_delivered":
            return Decision("answer", answer=f"订单{order['order_id']}还未签收,暂不能申请退货。"
                                             f"如需取消订单请联系客服。")
        if verdict == "eligible":  # 大额商品
            return Decision("escalate",
                            reason=f"订单{order['order_id']}金额{amount:.0f}元属于大额商品,"
                                   f"退货需人工审核(符合退货条件:{reason})",
                            recommended_action="人工审核大额商品退货申请,核实商品情况后处理")
        # overdue / category_blocked
        if amount >= 1000 or _quality_claim(ctx.user_input):
            return Decision("escalate",
                            reason=f"订单{order['order_id']}({reason})且金额{amount:.0f}元,"
                                   f"超过自动处理范围,需人工审核",
                            recommended_action="人工核实商品问题与售后期限,判断是否特事特办")
        return Decision("answer", answer=self._render_reject(order, reason, days))

    def _decide_refund(self, ctx: TaskContext) -> Decision:
        if "query_order" not in ctx.observations:
            return Decision("tool", tool="query_order", arguments={"user_id": ctx.user_id},
                            reason="查询订单与退款记录")
        data = ctx.observations["query_order"]["data"]
        order = self._select_order(ctx, (data or {}).get("orders", []), prefer="with_after_sale")
        if order is None:
            return Decision("answer", answer="没有查询到相关订单,请确认后重试。")
        self._remember(ctx, order)
        return Decision("answer", answer=self._render_refund(order))

    def _decide_human(self, ctx: TaskContext) -> Decision:
        return Decision("escalate", reason=ctx.user_input or "用户要求人工服务",
                        recommended_action="人工客服跟进用户诉求")

    def _decide_unknown(self, ctx: TaskContext) -> Decision:
        return Decision("answer",
                        answer="抱歉,我没有理解您的问题。您可以问我:订单什么时候发货、"
                               "快递到哪里了、售后政策、退货、退款,或者要求人工客服。")

    # ---- helpers -------------------------------------------------------
    def _remember(self, ctx: TaskContext, order: dict) -> None:
        ctx.note("order_id", order.get("order_id"))
        items = order.get("product")
        if items:
            ctx.note("product", items)

    def _select_order(self, ctx: TaskContext, orders: list[dict],
                      prefer: str | None = None) -> dict | None:
        if not orders:
            return None
        wanted = ctx.slots.get("order_id")
        if wanted:
            for order in orders:
                if order.get("order_id") == wanted:
                    return order
        keywords = _order_keywords(ctx.user_input)
        if keywords:
            matched = [o for o in orders
                       if any(k in (str(o.get("product") or "") + str(o.get("category") or ""))
                              for k in keywords)]
            if matched:
                return self._prefer(matched, prefer)
        if "昨天" in ctx.user_input:
            yesterday = (demo_today() - timedelta(days=1)).isoformat()
            dated = [o for o in orders if o.get("created_at") == yesterday]
            if dated:
                return dated[0]
        return self._prefer(orders, prefer)

    @staticmethod
    def _prefer(orders: list[dict], prefer: str | None) -> dict:
        def latest(pool: list[dict]) -> dict:
            return sorted(pool, key=lambda o: str(o.get("created_at") or ""), reverse=True)[0]

        if prefer == "with_after_sale":
            flagged = [o for o in orders if o.get("refunds") or o.get("after_sale_requests")]
            if flagged:
                return latest(flagged)
        elif prefer in ("paid", "shipped"):
            staged = [o for o in orders if o.get("status") == prefer]
            if staged:
                return latest(staged)
        else:
            delivered = [o for o in orders if o.get("status") == "delivered"]
            if delivered:
                return latest(delivered)
        return latest(orders)

    def _evaluate_return(self, ctx: TaskContext, order: dict) -> tuple[str, str, int]:
        """Return (verdict, reason, days_since_delivery)."""
        delivered_at = order.get("delivered_at")
        if not delivered_at:
            return "not_delivered", "订单未签收", 0
        days = (demo_today() - date.fromisoformat(delivered_at)).days
        quality = _quality_claim(ctx.user_input)
        category = str(order.get("category") or "")
        if category == "食品" and not quality:
            return "category_blocked", "食品类商品不支持无理由退货", days
        limit = self.quality_window_days if quality else self.no_reason_window_days
        label = "质量问题售后期限%s天" % limit if quality else "%s天无理由退货期限" % limit
        if days <= limit:
            return "eligible", f"签收{days}天,在{label}内", days
        return "overdue", f"签收{days}天,超过{label}", days

    # ---- renderers ------------------------------------------------------
    @staticmethod
    def _render_order(order: dict) -> str:
        status = {"paid": "已付款,待发货", "shipped": "已发货", "delivered": "已签收",
                  }.get(order.get("status"), order.get("status"))
        lines = [f"订单{order['order_id']}:{order.get('product') or '商品'}"
                 f"(金额{order.get('total')}元)—— {status},下单时间{order.get('created_at')}。"]
        if order.get("delivered_at"):
            lines.append(f"签收时间:{order['delivered_at']}。")
        else:
            lines.append("该订单尚未签收,预计付款后48小时内发货。")
        return "\n".join(lines)

    @staticmethod
    def _render_logistics(order: dict, logistics: dict) -> str:
        if not logistics.get("found"):
            return f"订单{order['order_id']}暂时没有物流信息,可能尚未发货。"
        return (f"订单{order['order_id']}的物流状态:{logistics.get('status')}"
                f"(承运商{logistics.get('carrier') or '无'},运单号{logistics.get('tracking_no') or '无'})。"
                f"最新轨迹:{logistics.get('updated_at') or '暂无'} {logistics.get('location') or ''}。")

    def _render_policy(self, ctx: TaskContext, order: dict | None,
                       policies: list[dict]) -> str:
        if not policies:
            return "暂时没有检索到相关的售后政策,您可以转人工客服咨询。"
        top = policies[0]
        lines = [f"根据《{top['policy']}》{('·' + top['section']) if top['section'] else ''}:",
                 top["text"].replace("\n", " ")]
        if order is not None:
            verdict, reason, _days = self._evaluate_return(ctx, order)
            verdict_text = {
                "eligible": f"结合您的订单{order['order_id']}({order.get('product')}),{reason},可以申请退货。",
                "overdue": f"结合您的订单{order['order_id']}({order.get('product')}),{reason},"
                           f"已超过自助退货期限,可转人工审核。",
                "category_blocked": f"结合您的订单{order['order_id']},{reason}。",
                "not_delivered": f"您的订单{order['order_id']}还未签收,暂不涉及退货时效。",
            }[verdict]
            lines.append(verdict_text)
        return "\n".join(lines)

    @staticmethod
    def _render_return_created(order: dict, result: dict) -> str:
        request = result.get("return_request") or {}
        return (f"退货申请已创建:申请单号{request.get('request_id')},"
                f"订单{order['order_id']}({order.get('product')}),原因:{request.get('reason')}。"
                f"退货物流与退款进度会通过消息通知您。")

    @staticmethod
    def _render_reject(order: dict, reason: str, days: int) -> str:
        return (f"很抱歉,订单{order['order_id']}({order.get('product')})签收已{days}天,"
                f"{reason},暂不符合自助退货条件。如有疑问可以转人工客服进一步核实。")

    @staticmethod
    def _render_refund(order: dict) -> str:
        refunds = order.get("refunds") or []
        returns = order.get("after_sale_requests") or []
        if refunds:
            refund = refunds[-1]
            return (f"订单{order['order_id']}的退款{refund['refund_id']}金额{refund['amount']}元,"
                    f"当前状态:{refund['status']}(处理中)。退款将在退货商品签收后3个工作日内"
                    f"原路退回,到账时间以支付渠道为准。")
        if returns:
            return (f"订单{order['order_id']}的退货申请{returns[-1]['request_id']}"
                    f"({returns[-1]['status']})正在处理,退货签收后3个工作日内会发起退款。")
        return f"订单{order['order_id']}目前没有退货或退款记录。"
