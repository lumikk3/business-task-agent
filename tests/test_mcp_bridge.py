"""MCP 桥接测试（Step 12）：真实拉起 MCP Server 走一次 stdio 往返。

没有安装 mcp 包时整体 skip（核心 Agent 不依赖它）。
"""
from __future__ import annotations

import pytest

from app.data.generator import DEFAULT_DB_PATH, generate

pytest.importorskip("mcp", reason="需要可选依赖 mcp：pip install -r requirements-optional.txt")

from app.mcp.bridge import MCPServerSpec, MCPToolBridge  # noqa: E402
from app.tools.registry import ToolExecutor  # noqa: E402

ORDER = "app.mcp.servers.order_server"
LOGISTICS = "app.mcp.servers.logistics_server"
AFTERSALE = "app.mcp.servers.aftersale_server"


@pytest.fixture(scope="module")
def bridge():
    if not DEFAULT_DB_PATH.exists():
        generate(DEFAULT_DB_PATH)
    db = str(DEFAULT_DB_PATH)
    br = MCPToolBridge([MCPServerSpec("order", ORDER, db),
                        MCPServerSpec("logistics", LOGISTICS, db),
                        MCPServerSpec("aftersale", AFTERSALE, db)])
    br.start()
    yield br
    br.stop()


def test_discovers_all_three_servers_and_their_tools(bridge):
    assert set(bridge.server_names()) == {"order", "logistics", "aftersale"}
    assert set(bridge.tools("order")) == {"query_order", "query_order_items"}
    assert set(bridge.tools("logistics")) == {"query_logistics", "query_delivery_status"}
    assert set(bridge.tools("aftersale")) == {
        "create_return_request", "create_refund_request", "create_human_ticket"}


def test_registry_exposes_mcp_tools_with_real_schemas(bridge):
    registry = bridge.to_registry()
    assert "query_order" in registry.names()
    spec = registry.get("query_order")
    # 关键：入参 schema 必须被正确解析,否则 ToolExecutor 会把参数过滤成空
    assert "user_id" in spec.parameters["properties"]
    assert spec.parameters["required"] == ["user_id"]
    assert registry.risk_level("create_refund_request").value == "HIGH"


def test_call_round_trips_through_stdio(bridge):
    executor = ToolExecutor(bridge.to_registry(), max_retries=0, timeout_s=30.0)
    order = executor.execute("query_order", {"user_id": "U10003"})
    assert order.ok and order.data["found"] is True

    logistics = executor.execute("query_logistics", {"order_id": "O10002"})
    assert logistics.ok and logistics.data["status"] == "运输中"
