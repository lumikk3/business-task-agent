"""部署产物一致性（Step 18 / 20）。

⚠️ 这些只校验「文件之间是一致的」,不校验「能在 Docker/Postgres 里跑起来」——
本机没有 docker / postgres,见 DEPLOY.md。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _tables(sql: str) -> set[str]:
    return set(re.findall(r"CREATE TABLE IF NOT EXISTS\s+(\w+)", sql))


def test_postgres_ddl_matches_init_sql():
    from app.store_pg import DDL
    init_sql = (ROOT / "docker" / "postgres" / "init.sql").read_text(encoding="utf-8")
    assert _tables(DDL) == _tables(init_sql)
    # 业务表 + Agent 侧表都要在（DESIGN.md 第 8 节）
    assert {"users", "orders", "products", "logistics", "after_sale_requests",
            "tickets", "agent_tasks", "agent_traces", "eval_cases"} <= _tables(DDL)


def test_store_pg_mirrors_sqlite_store_interface():
    from app.store import Store
    from app.store_pg import PgStore

    for method in ("find_orders", "get_order", "get_order_items", "get_logistics",
                   "get_after_sale", "get_refunds", "create_return_request",
                   "create_refund_request", "create_ticket"):
        assert hasattr(PgStore, method), method
        assert hasattr(Store, method), method


def test_db_modules_import_without_optional_deps():
    """没装 psycopg / redis 时,import 这两个模块也不应该炸（惰性导入）。"""
    import importlib

    importlib.import_module("app.store_pg")
    importlib.import_module("app.memory.redis_store")


def test_compose_declares_expected_services_and_dockerfiles_exist():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    services = set(re.findall(r"^  ([a-z][a-z0-9-]*):", compose, re.MULTILINE))
    assert {"postgres", "redis", "agent-api", "mcp-order", "mcp-logistics",
            "mcp-aftersale"} <= services
    assert (ROOT / "docker" / "Dockerfile.api").exists()
    assert (ROOT / "docker" / "Dockerfile.mcp").exists()


def test_requirements_files_list_optional_stack():
    optional = (ROOT / "requirements-optional.txt").read_text(encoding="utf-8")
    db = (ROOT / "requirements-db.txt").read_text(encoding="utf-8")
    assert "mcp" in optional and "fastapi" in optional and "uvicorn" in optional
    assert "psycopg" in db and "redis" in db


def test_mcp_servers_are_importable_and_register_tools():
    import importlib

    import pytest

    pytest.importorskip("mcp")
    expected = {"app.mcp.servers.order_server": 2,
                "app.mcp.servers.logistics_server": 2,
                "app.mcp.servers.aftersale_server": 3}
    for module_name, tool_count in expected.items():
        module = importlib.import_module(module_name)
        server = module.server
        assert server.name.endswith("mcp")
        # asyncio 里读一次注册的工具数
        import asyncio

        listed = asyncio.run(server.list_tools())
        assert len(listed) == tool_count, module_name
