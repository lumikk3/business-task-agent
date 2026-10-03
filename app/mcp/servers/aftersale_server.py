"""AfterSale MCP Server —— 售后域工具（写操作）。

    python -m app.mcp.servers.aftersale_server
"""
from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from app.mcp.servers.common import get_tools

server = MCPServer(name="aftersale-mcp", version="1.0",
                   description="售后域：退货 / 退款 / 人工工单")


@server.tool()
def create_return_request(order_id: str, reason: str) -> dict:
    """为订单创建退货申请。

    Args:
        order_id: 订单ID
        reason: 退货原因
    """
    return get_tools().create_return_request(order_id, reason)


@server.tool()
def create_refund_request(order_id: str, amount: float) -> dict:
    """为订单创建退款申请(金额受退款权限分级约束)。

    Args:
        order_id: 订单ID
        amount: 退款金额(元)
    """
    return get_tools().create_refund_request(order_id, amount)


@server.tool()
def create_human_ticket(user_id: str, reason: str, order_id: str | None = None,
                        recommended_action: str | None = None) -> dict:
    """创建人工工单,把任务转交人工客服。

    Args:
        user_id: 用户ID
        reason: 转人工原因
        order_id: 可选,关联订单ID
        recommended_action: 可选,建议处理方式
    """
    return get_tools().create_human_ticket(user_id, reason, order_id, recommended_action)


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
