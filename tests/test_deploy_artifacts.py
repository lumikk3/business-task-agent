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


def test_backend_factories_switch_purely_on_env(monkeypatch):
    """Step 18 的接线：只靠环境变量切换后端,默认不引入任何服务依赖。"""
    from app import backend
    from app.memory.redis_store import RedisSessionMemory
    from app.memory.session import SessionMemory
    from app.store import Store

    for var in ("DATABASE_URL", "POSTGRES_DSN", "REDIS_URL"):
        monkeypatch.delenv(var, raising=False)
    assert backend.backend_names() == {"business_store": "sqlite",
                                       "session_memory": "in-process"}
    assert isinstance(backend.open_business_store(scratch=True), Store)
    assert isinstance(backend.open_session_memory(), SessionMemory)

    # 只设 REDIS_URL 就切 Redis(构造时不连服务器,所以不需要真的起 Redis)
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6399/0")
    assert isinstance(backend.open_session_memory(), RedisSessionMemory)
    assert backend.backend_names()["session_memory"] == "redis"

    # 只设 DATABASE_URL 就切 Postgres（驱动缺失/连不上时才在实例化阶段报错）
    monkeypatch.delenv("REDIS_URL")
    monkeypatch.setenv("DATABASE_URL", "postgresql://x@127.0.0.1:1/x")
    assert backend.backend_names()["business_store"] == "postgres"


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
