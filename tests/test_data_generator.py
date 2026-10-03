"""业务数据生成器测试：规模、确定性、引用完整性、演示用户。"""
from __future__ import annotations

import sqlite3

from app.data.generator import (
    AFTER_SALES,
    LOGISTICS,
    ORDERS,
    PRODUCTS,
    USERS,
    build_dataset,
    generate,
)
from app.store import open_store
from app.tools.business import BusinessTools
from app.rag.policy_rag import PolicyRAG


def _counts(db_path) -> dict[str, int]:
    conn = sqlite3.connect(str(db_path))
    try:
        return {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("users", "products", "orders", "order_items",
                          "logistics", "after_sale_requests", "refunds")
        }
    finally:
        conn.close()


def test_generate_hits_exact_scale(tmp_path):
    db = tmp_path / "business.db"
    stats = generate(db)
    counts = _counts(db)
    assert counts["users"] == USERS == 1000
    assert counts["products"] == PRODUCTS == 3000
    assert counts["orders"] == ORDERS == 5000
    assert counts["logistics"] == LOGISTICS == 5000
    assert counts["after_sale_requests"] == AFTER_SALES == 500
    assert counts["order_items"] == ORDERS
    assert stats["counts"]["orders"] == ORDERS


def test_generate_is_deterministic_for_same_seed(tmp_path):
    a = build_dataset(seed=7)
    b = build_dataset(seed=7)
    assert a["orders"] == b["orders"]
    assert a["logistics"] == b["logistics"]
    assert a["after_sale_requests"] == b["after_sale_requests"]
    # 不同 seed 应当给出不同数据
    assert build_dataset(seed=8)["orders"] != a["orders"]


def test_referential_integrity(tmp_path):
    data = build_dataset()
    user_ids = {u[0] for u in data["users"]}
    product_ids = {p[0] for p in data["products"]}
    order_by_id = {o[0]: o for o in data["orders"]}
    ordered_ids = set(order_by_id)

    # 订单 -> 用户存在
    assert all(o[1] in user_ids for o in data["orders"])
    # order_items -> 订单 / 商品 均存在
    assert all(i[0] in ordered_ids and i[1] in product_ids for i in data["order_items"])
    # 每张订单恰好一条物流
    log_order_ids = [l[0] for l in data["logistics"]]
    assert len(log_order_ids) == len(set(log_order_ids)) == len(data["orders"])
    assert set(log_order_ids) == ordered_ids
    # 售后只挂在 delivered 订单上
    assert all(order_by_id[r[1]][2] == "delivered" for r in data["after_sale_requests"])
    # 退款都指向已存在的售后单
    request_ids = {r[0] for r in data["after_sale_requests"]}
    assert all(rf[1] in request_ids for rf in data["refunds"])


def test_demo_users_are_clean_and_answer_the_three_questions(tmp_path):
    """三个演示用户各只有一张订单，状态刚好对应三个展示问题。"""
    db = tmp_path / "business.db"
    generate(db)
    store = open_store(str(db), seed=False)
    tools = BusinessTools(store, policy_search=PolicyRAG().retrieve)

    expected = {"U10001": "paid", "U10002": "shipped", "U10003": "delivered"}
    for uid, status in expected.items():
        orders = tools.query_order(uid)["orders"]
        assert len(orders) == 1, f"{uid} 应只有一张演示订单"
        assert orders[0]["status"] == status
        # 演示订单必须干净：不能预置历史售后/退款,否则展示场景会串味
        assert orders[0]["after_sale_requests"] == []
        assert orders[0]["refunds"] == []

    # U10002 的订单有在途物流
    logistics = tools.query_logistics("O10002")
    assert logistics["found"] is True
    assert logistics["status"] == "运输中"

    # U10003 是一台已签收的耳机，且 RAG 能检索到耳机政策
    order = tools.query_order("U10003")["orders"][0]
    assert order["category"] == "耳机"
    assert order["delivered_at"] is not None
    policies = tools.search_after_sales_policy("这个耳机能退吗")["policies"]
    assert any("耳机" in p["policy"] for p in policies)


def test_generate_overwrites_existing_db(tmp_path):
    db = tmp_path / "business.db"
    generate(db)
    generate(db)  # 重复执行不应报错，也不应重复累加行
    assert _counts(db)["orders"] == ORDERS
