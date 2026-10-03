"""Intent recognition (DESIGN.md 5.2).

Seven intents: ORDER_QUERY / LOGISTICS_QUERY / AFTERSALE_POLICY /
RETURN_REQUEST / REFUND_QUERY / HUMAN_SERVICE / UNKNOWN.

V1 uses ordered regex rules — deterministic and testable. The recognizer is
behind a small interface so an LLM-based classifier can replace it later.
"""
from __future__ import annotations

import re
from enum import Enum


class Intent(str, Enum):
    ORDER_QUERY = "ORDER_QUERY"
    LOGISTICS_QUERY = "LOGISTICS_QUERY"
    AFTERSALE_POLICY = "AFTERSALE_POLICY"
    RETURN_REQUEST = "RETURN_REQUEST"
    REFUND_QUERY = "REFUND_QUERY"
    HUMAN_SERVICE = "HUMAN_SERVICE"
    UNKNOWN = "UNKNOWN"


# First match wins — order encodes priority (action requests before queries).
_PATTERNS: list[tuple[re.Pattern, Intent]] = [
    (re.compile(r"人工|投诉|客服|转人工"), Intent.HUMAN_SERVICE),
    (re.compile(r"退货|换货|退换|想退|要退|申请退|退了"), Intent.RETURN_REQUEST),
    (re.compile(r"退款|退钱|到账|退回来"), Intent.REFUND_QUERY),
    (re.compile(r"政策|规则|还能退|能退|可以退吗|保修|几天内|售后|能不能"), Intent.AFTERSALE_POLICY),
    (re.compile(r"快递|物流|到哪|到货|配送|签收|包裹"), Intent.LOGISTICS_QUERY),
    (re.compile(r"订单|发货|下单|购买"), Intent.ORDER_QUERY),
]


class RuleBasedIntentRecognizer:
    def recognize(self, text: str) -> Intent:
        for pattern, intent in _PATTERNS:
            if pattern.search(text):
                return intent
        return Intent.UNKNOWN
