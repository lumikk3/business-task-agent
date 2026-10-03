from app.rag.policy_rag import PolicyRAG
from app.store import Store, reset_store
from app.tools.business import BusinessTools, build_registry
from app.tools.registry import ToolExecutor


def make_tools():
    store = reset_store()
    rag = PolicyRAG()
    return BusinessTools(store, policy_search=rag.retrieve), store, rag


def test_query_order_returns_enriched_orders():
    tools, _store, _rag = make_tools()
    result = tools.query_order("U10001")
    assert result["found"] is True
    assert len(result["orders"]) == 3
    first = result["orders"][0]
    assert {"order_id", "product", "category", "after_sale_requests", "refunds"} <= set(first)


def test_query_order_includes_after_sale_and_refund_records():
    tools, _store, _rag = make_tools()
    result = tools.query_order("U10001", order_id="O202609002")
    order = result["orders"][0]
    assert order["after_sale_requests"][0]["request_id"] == "R202609001"
    assert order["refunds"][0]["refund_id"] == "RF202609001"


def test_query_logistics():
    tools, _store, _rag = make_tools()
    result = tools.query_logistics("O202609004")
    assert result["found"] is True
    assert result["status"] == "运输中"
    assert tools.query_logistics("NOPE")["found"] is False


def test_search_after_sales_policy_wired_to_rag():
    tools, _store, _rag = make_tools()
    result = tools.search_after_sales_policy("耳机用了5天还能退吗")
    assert result["found"] is True
    assert any("耳机" in p["policy"] or "7天" in p["policy"] for p in result["policies"])


def test_create_return_request():
    tools, _store, _rag = make_tools()
    result = tools.create_return_request("O202609001", "耳机坏了")
    assert result["success"] is True
    assert result["return_request"]["status"] == "created"
    assert tools.create_return_request("NOPE", "x")["success"] is False


def test_create_refund_request_validates_amount():
    tools, _store, _rag = make_tools()
    assert tools.create_refund_request("O202609002", -1)["success"] is False
    assert tools.create_refund_request("O202609002", 9999)["success"] is False
    result = tools.create_refund_request("O202609002", 100)
    assert result["success"] is True
    assert result["refund_request"]["amount"] == 100


def test_create_human_ticket():
    tools, _store, _rag = make_tools()
    result = tools.create_human_ticket("U10002", "超过售后期限且金额超过1000元",
                                       order_id="O202609003",
                                       recommended_action="人工审核")
    assert result["success"] is True
    assert result["ticket"]["ticket_id"].startswith("T")


def test_registry_exposes_all_six_tools_with_risk_levels():
    tools, _store, _rag = make_tools()
    registry = build_registry(tools)
    assert set(registry.names()) == {
        "query_order", "query_logistics", "search_after_sales_policy",
        "create_return_request", "create_refund_request", "create_human_ticket",
    }
    assert registry.risk_level("query_order").value == "LOW"
    assert registry.risk_level("create_return_request").value == "MEDIUM"
    assert registry.risk_level("create_refund_request").value == "HIGH"


def test_executor_runs_business_tool_end_to_end():
    tools, _store, _rag = make_tools()
    registry = build_registry(tools)
    executor = ToolExecutor(registry, sleep=lambda _s: None)
    result = executor.execute("query_order", {"user_id": "U10001"})
    assert result.ok is True
    assert result.data["found"] is True
