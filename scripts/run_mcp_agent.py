#!/usr/bin/env python3
"""Step 12 演示：业务工具改造成 MCP,Agent 通过 MCP Client 调用。

    Agent -> MCP Client -> Order MCP / Logistics MCP / AfterSale MCP -> Business DB

三个 MCP Server 是独立进程（stdio），Agent 侧完全无感：拿到的仍是标准的
ToolRegistry，权限门 / Trace / 并发照常工作。

    python scripts/run_mcp_agent.py            # 需要 GLM_API_KEY
    python scripts/run_mcp_agent.py --probe    # 只探测工具（不需要 LLM）
"""
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.agent.llm_client import ChatClient, has_llm_credentials, resolve_llm_config  # noqa: E402
from app.agent.minimal_agent import MinimalAgent  # noqa: E402
from app.agent.permissions import PermissionPolicy  # noqa: E402
from app.data.generator import DEFAULT_DB_PATH, generate  # noqa: E402
from app.mcp.bridge import MCPServerSpec, MCPToolBridge  # noqa: E402
from app.tools.registry import ToolExecutor  # noqa: E402

QUESTION = "我的耳机坏了,我想退货。"
USER_ID = "U10003"

SPECS = [
    MCPServerSpec("order", "app.mcp.servers.order_server", ""),
    MCPServerSpec("logistics", "app.mcp.servers.logistics_server", ""),
    MCPServerSpec("aftersale", "app.mcp.servers.aftersale_server", ""),
]


def _scratch_db() -> str:
    if not Path(DEFAULT_DB_PATH).exists():
        generate(DEFAULT_DB_PATH)
    scratch = Path(tempfile.mkdtemp(prefix="mcp-db-")) / "business.db"
    shutil.copyfile(DEFAULT_DB_PATH, scratch)
    return str(scratch)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", action="store_true", help="只列出 MCP 工具")
    args = parser.parse_args()

    db_path = _scratch_db()
    bridge = MCPToolBridge([MCPServerSpec(s.name, s.module, db_path) for s in SPECS])
    bridge.start()
    try:
        print(f"[mcp] 已连接 {len(bridge.server_names())} 个 MCP Server")
        for server in bridge.server_names():
            names = ", ".join(bridge.tools(server))
            print(f"  - {server}-mcp: {names}")

        registry = bridge.to_registry()
        print(f"[tools] {', '.join(registry.names())}")

        if args.probe:
            result = registry.get("query_logistics").handler(order_id="O10002")
            print(f"[probe] query_logistics(O10002) -> {result}")
            return

        if not has_llm_credentials():
            print("\n[error] 未找到 GLM_API_KEY,加 --probe 可只探测工具。")
            raise SystemExit(2)

        executor = ToolExecutor(registry, max_retries=2, timeout_s=30.0)
        agent = MinimalAgent(ChatClient(resolve_llm_config()), registry, executor,
                             permission_policy=PermissionPolicy(),
                             trace_dir=ROOT / "traces")
        print(f"\n用户   : {QUESTION}")
        result = agent.run(QUESTION, user_id=USER_ID, session_id="mcp-demo")
        for step in result.steps:
            if step["type"] == "tool":
                print(f"  step{step['step']}  [MCP] {step['tool']}({step['arguments']}) -> "
                      f"{'ok' if step['ok'] else 'FAIL'}")
        print(f"状态   : {result.status}")
        print(f"回答   : {result.answer}")
    finally:
        bridge.stop()


if __name__ == "__main__":
    main()
