"""异步 / 并发执行（Step 19）。

同一轮里多个**互不依赖**的工具调用（查订单、查物流、查商品）不必串行：

    Agent ──┬──> Order
            ├──> Logistics
            └──> Product

`gather()` 用线程池并发执行；`gather_async()` 是 asyncio 版本（`asyncio.gather`）。
工具本身是阻塞函数，所以并发靠线程池；要真异步需要工具层也 async 化。
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor

from app.tools.registry import ToolExecutor, ToolResult


def gather(executor: ToolExecutor, calls: list[tuple[str, dict]],
           max_workers: int | None = None) -> list[ToolResult]:
    """并发执行多个工具调用，**按输入顺序**返回结果。"""
    if not calls:
        return []
    if len(calls) == 1:
        name, arguments = calls[0]
        return [executor.execute(name, arguments)]
    with ThreadPoolExecutor(max_workers=max_workers or len(calls)) as pool:
        futures = [pool.submit(executor.execute, name, arguments)
                   for name, arguments in calls]
        return [future.result() for future in futures]


def run_sequential(executor: ToolExecutor, calls: list[tuple[str, dict]]) -> list[ToolResult]:
    return [executor.execute(name, arguments) for name, arguments in calls]


async def gather_async(executor: ToolExecutor, calls: list[tuple[str, dict]],
                       max_workers: int | None = None) -> list[ToolResult]:
    """asyncio 版本：把阻塞的工具调用丢到线程池,再 asyncio.gather 收口。"""
    if not calls:
        return []
    return await asyncio.gather(*[
        asyncio.to_thread(executor.execute, name, arguments) for name, arguments in calls
    ])
