"""运行时后端选择（Step 18）。

一处决定「用 SQLite 还是 PostgreSQL」「用进程内 dict 还是 Redis」，靠环境变量切换：

    DATABASE_URL=postgresql://hermes@127.0.0.1:55432/business   -> PgStore
    REDIS_URL=redis://127.0.0.1:6399/0                          -> RedisSessionMemory

不设任何变量时保持原来的行为（SQLite + 进程内记忆），所以本机/CI 不需要任何服务。
"""
from __future__ import annotations

import os
from pathlib import Path

from app.data.generator import DEFAULT_DB_PATH
from app.store import open_scratch_store, open_store


def database_url() -> str | None:
    return os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_DSN") or None


def redis_url() -> str | None:
    return os.environ.get("REDIS_URL") or None


def backend_names() -> dict[str, str]:
    return {
        "business_store": "postgres" if database_url() else "sqlite",
        "session_memory": "redis" if redis_url() else "in-process",
    }


def open_business_store(*, sqlite_path: str | Path | None = None, seed: bool = False,
                        scratch: bool = False):
    """业务数据后端：有 DATABASE_URL 走 PostgreSQL，否则走 SQLite。

    ``scratch=True`` 时（demo/脚本场景）在 SQLite 生成库的临时副本上操作，
    避免写脏被提交的 data/business.db —— PostgreSQL 不需要，因为它本来就是
    独立的库，而且是可重建的（migrate_to_pg.py 幂等）。
    """
    if database_url():
        from app.store_pg import PgStore
        return PgStore()
    if scratch:
        return open_scratch_store(str(sqlite_path) if sqlite_path else None, seed=seed)
    return open_store(str(sqlite_path) if sqlite_path else None, seed=seed)


def open_session_memory(*, ttl_seconds: int | None = None):
    """会话记忆后端：有 REDIS_URL 走 Redis，否则进程内 dict。"""
    if redis_url():
        from app.memory.redis_store import RedisSessionMemory
        kwargs = {"ttl_seconds": ttl_seconds} if ttl_seconds else {}
        return RedisSessionMemory(**kwargs)
    from app.memory.session import SessionMemory
    return SessionMemory()


def default_sqlite_path() -> str:
    return str(DEFAULT_DB_PATH)


def ensure_sqlite_seed() -> None:
    """SQLite 模式下确保生成库存在（PostgreSQL 模式下不需要）。"""
    if database_url():
        return
    if not Path(DEFAULT_DB_PATH).exists():
        from app.data.generator import generate
        generate(DEFAULT_DB_PATH)
