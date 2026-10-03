"""故意制造错误（Step 10）—— 给工具注入 timeout / error。

真实的业务系统不会永远成功，所以这里主动制造失败，用来验证：

    Tool Call -> Timeout -> Retry -> Retry -> 仍失败 -> Fallback -> Human Ticket

默认比例：10% timeout、5% error（可用参数覆盖）。用固定 seed 保证可复现。
``inject_faults`` 返回一个包裹后的**新**注册表，不改动原注册表。
"""
from __future__ import annotations

import random
from dataclasses import replace

from app.tools.registry import ErrorType, ToolError, ToolRegistry, ToolSpec

DEFAULT_TIMEOUT_RATE = 0.10
DEFAULT_ERROR_RATE = 0.05


def _wrap(spec: ToolSpec, rng: random.Random,
          timeout_rate: float, error_rate: float) -> ToolSpec:
    original = spec.handler

    def handler(**kwargs):
        roll = rng.random()
        if roll < timeout_rate:
            raise ToolError(f"[injected] {spec.name} 上游超时", ErrorType.TIMEOUT,
                            retryable=True)
        if roll < timeout_rate + error_rate:
            raise ToolError(f"[injected] {spec.name} 上游返回错误", ErrorType.EXECUTION,
                            retryable=True)
        return original(**kwargs)

    return replace(spec, handler=handler)


def inject_faults(registry: ToolRegistry,
                  timeout_rate: float = DEFAULT_TIMEOUT_RATE,
                  error_rate: float = DEFAULT_ERROR_RATE,
                  seed: int | None = 20260927,
                  tools: tuple[str, ...] | None = None) -> ToolRegistry:
    """返回注入了故障的新注册表。

    ``tools`` 限定只对哪些工具注入（例如只让 ``query_order`` 抖），其余工具保持健康 ——
    这样 create_human_ticket 这类兜底工具不会被一起打挂。
    """
    rng = random.Random(seed)
    faulty = ToolRegistry()
    for spec in registry.specs():
        if tools is not None and spec.name not in tools:
            faulty.register(spec)
            continue
        faulty.register(_wrap(spec, rng, timeout_rate, error_rate))
    return faulty
