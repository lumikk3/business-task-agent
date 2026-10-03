"""三个 MCP Server 共用的数据装配。

每个 Server 是独立进程，用 ``AGENT_DB_PATH`` 指向同一个业务库。
"""
from __future__ import annotations

import os
from functools import lru_cache

from app.data.generator import DEFAULT_DB_PATH
from app.rag.policy_rag import PolicyRAG
from app.store import open_store
from app.tools.business import BusinessTools


@lru_cache(maxsize=1)
def get_tools() -> BusinessTools:
    path = os.environ.get("AGENT_DB_PATH") or str(DEFAULT_DB_PATH)
    store = open_store(path, seed=False)
    return BusinessTools(store, policy_search=PolicyRAG().retrieve)
