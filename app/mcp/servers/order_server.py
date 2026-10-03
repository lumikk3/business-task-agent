"""Order MCP Server —— 订单域工具。

    python -m app.mcp.servers.order_server
"""
from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from app.mcp.servers.common import get_tools

server = MCPServer(name="order-mcp", version="1.0",
                   description="订单域：查询订单与订单商品明细")


@server.tool()
def query_order(user_id: str, order_id: str | None = None) -> dict:
    """查询用户订单,返回商品、状态、售后与退款记录。

    Args:
        user_id: 用户ID,例如 U10001
        order_id: 可选,指定订单ID
    """
    return get_tools().query_order(user_id, order_id)


@server.tool()
def query_order_items(order_id: str) -> dict:
    """查询订单的商品明细(商品名、品类、数量、单价)。

    Args:
        order_id: 订单ID
    """
    items = get_tools().store.get_order_items(order_id)
    return {"order_id": order_id, "found": bool(items), "items": items}


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
