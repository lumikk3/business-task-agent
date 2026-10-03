"""MCP Client 桥接（Step 12）。

把若干 stdio MCP Server 挂到一个后台事件循环上，暴露**同步**接口，并转换成
Agent 直接可用的 ``ToolRegistry``：

    Agent -> MCP Client -> Order MCP / Logistics MCP / AfterSale MCP -> DB

这样 Agent 完全不知道工具是本地函数还是 MCP 远端 —— 这正是把工具 MCP 化的意义：
工具可以独立进程、独立语言、独立发布。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from app.tools.registry import ErrorType, RiskLevel, ToolError, ToolRegistry, ToolSpec

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# MCP 工具本身不带风险等级,由客户端按业务语义映射（也可由 Server 的 meta 提供）
RISK_BY_TOOL: dict[str, RiskLevel] = {
    "query_order": RiskLevel.LOW,
    "query_order_items": RiskLevel.LOW,
    "query_logistics": RiskLevel.LOW,
    "query_delivery_status": RiskLevel.LOW,
    "create_return_request": RiskLevel.MEDIUM,
    "create_refund_request": RiskLevel.HIGH,
    "create_human_ticket": RiskLevel.LOW,
}


@dataclass
class MCPServerSpec:
    name: str
    module: str                      # 例如 app.mcp.servers.order_server
    db_path: str

    def parameters(self) -> StdioServerParameters:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(PROJECT_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
        env["AGENT_DB_PATH"] = self.db_path
        return StdioServerParameters(command=sys.executable, args=["-m", self.module],
                                     env=env)


def _is_error(result) -> bool:
    return bool(getattr(result, "isError", None) or getattr(result, "is_error", None))


def _extract(result) -> dict:
    """把 MCP CallToolResult 归一化成 dict。"""
    structured = getattr(result, "structuredContent", None) or \
        getattr(result, "structured_content", None)
    if structured:
        return structured
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            try:
                parsed = json.loads(text)
                return parsed if isinstance(parsed, dict) else {"result": parsed}
            except json.JSONDecodeError:
                return {"text": text}
    return {}


def _tool_schema(tool) -> dict:
    """MCP Tool 的入参 schema：mcp 2.x 用 snake_case 的 ``input_schema``。"""
    schema = getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", None)
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}}
    return schema


class MCPToolBridge:
    def __init__(self, specs: list[MCPServerSpec], call_timeout: float = 60.0):
        self._specs = specs
        self._call_timeout = call_timeout
        self._sessions: dict[str, ClientSession] = {}
        self._tools: dict[str, dict[str, object]] = {}   # server -> {tool_name: Tool}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._stop: asyncio.Event | None = None
        self._error: BaseException | None = None

    # ---- lifecycle ----------------------------------------------------
    def start(self) -> "MCPToolBridge":
        self._thread = threading.Thread(target=self._thread_main, daemon=True)
        self._thread.start()
        if not self._ready.wait(60):
            raise RuntimeError("MCP servers did not become ready in time")
        if self._error is not None:
            raise self._error
        return self

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_until_complete(self._serve())
        except BaseException as exc:                 # noqa: BLE001 - 上报给 start()
            self._error = exc
            self._ready.set()
        finally:
            loop.close()

    async def _serve(self) -> None:
        try:
            async with AsyncExitStack() as stack:
                self._stop = asyncio.Event()
                for spec in self._specs:
                    read, write = await stack.enter_async_context(
                        stdio_client(spec.parameters()))
                    session = await stack.enter_async_context(ClientSession(read, write))
                    await session.initialize()
                    listed = await session.list_tools()
                    self._sessions[spec.name] = session
                    self._tools[spec.name] = {tool.name: tool for tool in listed.tools}
                self._ready.set()
                await self._stop.wait()
        except BaseException as exc:                 # noqa: BLE001
            self._error = exc
            self._ready.set()

    def stop(self) -> None:
        if self._stop is not None and self._loop is not None:
            self._loop.call_soon_threadsafe(self._stop.set)
        if self._thread is not None:
            self._thread.join(timeout=15)

    # ---- client API ---------------------------------------------------
    def server_names(self) -> list[str]:
        return list(self._sessions)

    def tools(self, server: str) -> dict[str, object]:
        return self._tools.get(server, {})

    def all_tools(self) -> list[tuple[str, str, dict]]:
        """返回 [(server, tool_name, tool)]。"""
        return [(server, name, tool) for server, tools in self._tools.items()
                for name, tool in tools.items()]

    def call(self, server: str, tool: str, arguments: dict) -> dict:
        session = self._sessions[server]
        future = asyncio.run_coroutine_threadsafe(
            session.call_tool(tool, arguments), self._loop)
        result = future.result(timeout=self._call_timeout)
        if _is_error(result):
            # MCP 层错误必须变成失败结果,否则 Agent 会把「调用失败」当成「成功」
            raise ToolError(f"mcp tool {server}/{tool} failed: {_extract(result)}",
                            ErrorType.EXECUTION, retryable=False)
        return _extract(result)

    # ---- adapt to the agent's tool layer ------------------------------
    def to_registry(self) -> ToolRegistry:
        registry = ToolRegistry()
        for server, name, tool in self.all_tools():
            registry.register(ToolSpec(
                name=name,
                description=getattr(tool, "description", "") or "",
                parameters=_tool_schema(tool),
                handler=_make_handler(self, server, name),
                risk_level=RISK_BY_TOOL.get(name, RiskLevel.LOW),
            ))
        return registry


def _make_handler(bridge: MCPToolBridge, server: str, tool: str):
    def handler(**kwargs):
        return bridge.call(server, tool, kwargs)
    return handler
