"""Redis 会话存储（Step 18）。

⚠️ 本机没有 Redis 服务,这个模块**未在本机运行验证过**。

用途：把 Step 9 的 ``SessionMemory`` 从进程内 dict 换成 Redis,这样多副本部署时
Session / Agent Context / Task State 能共享。

接口与 ``app.memory.session.SessionMemory`` 完全一致（``observe`` / ``context_block`` /
``remember_turn`` / ``note`` / ``slots``），因此 Agent 侧换一行构造即可。

redis 是惰性导入的：没装也不影响 import 本模块。
"""
from __future__ import annotations

import json
import os

from app.memory.session import SessionMemory


def redis_url() -> str:
    return os.environ.get("REDIS_URL", "redis://localhost:6379/0")


class RedisSessionMemory(SessionMemory):
    """用 Redis Hash 存会话（key: ``agent:session:<id>``）,TTL 可配。

    继承 ``SessionMemory`` 复用 ``observe`` / ``context_block`` / ``remember_turn``
    的全部逻辑,只把底层 ``_sessions`` 的读写换成 Redis。
    """

    def __init__(self, client=None, ttl_seconds: int = 3600):
        super().__init__()
        if client is not None:
            self._redis = client
        else:
            try:
                import redis                    # 惰性导入
            except ImportError as exc:          # pragma: no cover
                raise RuntimeError(
                    "Redis 支持需要 redis-py：pip install -r requirements-db.txt"
                ) from exc
            self._redis = redis.Redis.from_url(redis_url(), decode_responses=True)
        self._ttl = ttl_seconds

    def _key(self, session_id: str) -> str:
        return f"agent:session:{session_id}"

    def session(self, session_id: str) -> dict:
        raw = self._redis.get(self._key(session_id))
        if raw:
            return json.loads(raw)
        fresh = {"history": [], "slots": {}, "seen": {}}
        self._save(session_id, fresh)
        return fresh

    def _save(self, session_id: str, data: dict) -> None:
        self._redis.set(self._key(session_id), json.dumps(data, ensure_ascii=False),
                        ex=self._ttl)

    # 覆写写入路径,写回 Redis
    def observe(self, session_id: str, tool: str, data: dict | None) -> None:
        state = self.session(session_id)
        self._sessions[session_id] = state          # 复用父类逻辑
        super().observe(session_id, tool, data)
        self._save(session_id, self._sessions[session_id])

    def note(self, session_id: str, key: str, value) -> None:
        state = self.session(session_id)
        state["slots"][key] = value
        self._save(session_id, state)

    def remember_turn(self, session_id: str, user_input: str, answer: str,
                      intent: str | None = None) -> None:
        state = self.session(session_id)
        self._sessions[session_id] = state
        super().remember_turn(session_id, user_input, answer, intent)
        self._save(session_id, self._sessions[session_id])
