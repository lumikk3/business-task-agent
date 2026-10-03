"""Logistics MCP Server —— 物流域工具。

    python -m app.mcp.servers.logistics_server
"""
from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from app.mcp.servers.common import get_tools

server = MCPServer(name="logistics-mcp", version="1.0",
                   description="物流域：查询订单物流状态与轨迹")


@server.tool()
def query_logistics(order_id: str) -> dict:
    """查询订单的物流状态、承运商与最新轨迹。

    Args:
        order_id: 订单ID
    """
    return get_tools().query_logistics(order_id)


@server.tool()
def query_delivery_status(order_id: str) -> dict:
    """只返回订单的配送状态(已签收/运输中/待发货/已取消)。

    Args:
        order_id: 订单ID
    """
    info = get_tools().query_logistics(order_id)
    return {"order_id": order_id, "found": info.get("found"),
            "status": info.get("status")}


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
