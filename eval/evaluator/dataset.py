"""评测数据集（Step 14）：100~300 条测试 Case。

每个 intent 一组措辞（每种措辞就是一条真实输入），再配固定演示用户，
确定性地生成 200 条 Case。每条 Case 带期望意图、期望工具、期望状态、期望订单、
以及「绝不能调用的工具」（用于 Policy Compliance）。
"""
from __future__ import annotations

import json
import random
from pathlib import Path

DATASET_PATH = Path(__file__).resolve().parents[1] / "dataset" / "cases.jsonl"
SEED = 20260927
TARGET = 200

# 演示用户 -> 生成库里的订单（见 app/data/generator.py::DEMO_USERS）
USER_ORDER = {"U10001": "O10001", "U10002": "O10002", "U10003": "O10003"}

PHRASES: dict[str, list[str]] = {
    "ORDER_QUERY": [
        "我的订单什么时候发货?", "订单发货了吗", "帮我查一下订单", "我的订单处理好了吗",
        "订单状态查一下", "我下单的东西什么时候发", "看看我的订单", "订单还没有发货吗",
        "查下我的购买记录", "我的订单处理得怎么样了", "订单发货了没有", "我购买的东西什么时候寄出",
    ],
    "LOGISTICS_QUERY": [
        "我的快递到哪里了?", "快递到哪了", "物流信息查一下", "我的包裹在哪",
        "快递什么时候到", "帮我查物流", "我的快递到货了吗", "包裹配送到哪了",
        "物流更新了吗", "快递签收了吗", "我的快递现在什么状态", "查询配送进度",
    ],
    "AFTERSALE_POLICY": [
        "耳机用了5天还能退吗?", "这个耳机能退吗", "售后期是多久", "食品类商品适用什么售后规则",
        "质量问题能退几天", "保修几天内有效", "超过7天还能退吗", "售后政策怎么规定的",
        "耳机类商品能退吗", "售后规则是怎么规定的", "售后期限是多长时间", "这个还能不能退",
    ],
    "RETURN_REQUEST": [
        "耳机坏了,我要退货", "我想退货", "耳机坏了想退", "申请退货",
        "这个耳机我不要了,退了吧", "帮我办理退货", "我要退掉这个订单", "耳机有故障,退货",
        "直接给我退货", "我要申请退换货", "耳机不能用,想退货", "帮我把这个退了",
    ],
    "REFUND_QUERY": [
        "商品已经退回去了,什么时候退款?", "什么时候退款", "退款到账了吗", "退钱要多久",
        "钱退回来了吗", "查询退款进度", "什么时候能退钱", "退款处理到哪一步了",
        "我的退款还没到", "退款多久到账", "查一下退款状态", "退款的钱到哪了",
    ],
    "HUMAN_SERVICE": [
        "我要转人工", "转人工", "我要投诉", "找人工客服", "客服在吗", "投诉这个订单",
        "我要找人工", "人工服务", "这个我要投诉", "让客服联系我", "转接人工客服",
        "我要跟客服沟通",
    ],
    "UNKNOWN": [
        "今天天气怎么样", "讲个笑话", "你是谁", "帮我写首诗", "1+1等于几",
        "推荐一部电影", "现在几点了", "你会做饭吗", "随便聊聊", "唱首歌",
        "明天会下雨吗", "你好",
    ],
}

# intent -> 可用演示用户（确保期望结果稳定）
USERS: dict[str, list[str]] = {
    "ORDER_QUERY": ["U10001", "U10002", "U10003"],
    "LOGISTICS_QUERY": ["U10001", "U10002", "U10003"],
    "AFTERSALE_POLICY": ["U10001", "U10002", "U10003"],
    "RETURN_REQUEST": ["U10003"],          # 只有 U10003 是已签收的耳机,退货才会真正创建
    "REFUND_QUERY": ["U10001", "U10002", "U10003"],
    "HUMAN_SERVICE": ["U10001", "U10002", "U10003"],
    "UNKNOWN": ["U10001", "U10002", "U10003"],
}

EXPECTED_TOOLS: dict[str, list[str]] = {
    "ORDER_QUERY": ["query_order"],
    "LOGISTICS_QUERY": ["query_order", "query_logistics"],
    "AFTERSALE_POLICY": ["query_order", "search_after_sales_policy"],
    "RETURN_REQUEST": ["query_order", "search_after_sales_policy", "create_return_request"],
    "REFUND_QUERY": ["query_order"],
    "HUMAN_SERVICE": ["create_human_ticket"],
    "UNKNOWN": [],
}

EXPECTED_STATUS: dict[str, str] = {
    "ORDER_QUERY": "completed",
    "LOGISTICS_QUERY": "completed",
    "AFTERSALE_POLICY": "completed",
    "RETURN_REQUEST": "completed",
    "REFUND_QUERY": "completed",
    "HUMAN_SERVICE": "escalated",
    "UNKNOWN": "completed",
}

WRITE_TOOLS = ("create_return_request", "create_refund_request", "create_human_ticket")


def _forbidden(intent: str) -> list[str]:
    if intent == "RETURN_REQUEST":
        return ["create_refund_request"]                  # 退货绝不能变成退款
    if intent == "HUMAN_SERVICE":
        return ["create_return_request", "create_refund_request"]   # 工单本身是期望动作
    if intent in ("AFTERSALE_POLICY", "UNKNOWN"):
        return list(WRITE_TOOLS)                          # 咨询/闲聊不该产生任何写操作
    return list(WRITE_TOOLS)


def build_cases(target: int = TARGET, seed: int = SEED) -> list[dict]:
    raw: list[dict] = []
    for intent, phrases in PHRASES.items():
        for phrase in phrases:
            for user in USERS[intent]:
                raw.append({
                    "input": phrase,
                    "user_id": user,
                    "expected_intent": intent,
                    "expected_tools": list(EXPECTED_TOOLS[intent]),
                    "expected_status": EXPECTED_STATUS[intent],
                    "expected_order_id": USER_ORDER.get(user),
                    "forbidden_tools": _forbidden(intent),
                })
    random.Random(seed).shuffle(raw)
    cases = raw[:target]
    for index, case in enumerate(cases, start=1):
        case["id"] = f"case{index:03d}"
    return cases


def write_cases(path: Path | str = DATASET_PATH, target: int = TARGET,
                seed: int = SEED) -> int:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    cases = build_cases(target=target, seed=seed)
    with path.open("w", encoding="utf-8") as handle:
        for case in cases:
            handle.write(json.dumps(case, ensure_ascii=False) + "\n")
    return len(cases)


def load_cases(path: Path | str = DATASET_PATH) -> list[dict]:
    path = Path(path)
    if not path.exists():
        write_cases(path)
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
