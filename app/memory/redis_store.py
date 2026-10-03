"""Redis 会话存储（Step 18）—— 把 Step 9 的进程内 ``SessionMemory`` 换成 Redis。

接口与 ``app.memory.session.SessionMemory`` 完全一致（``observe`` / ``note`` /
``remember_turn`` / ``slots`` / ``context_block``），所以 Agent 侧换一行构造即可。
多副本部署时 Session / Agent Context / Task State 因此能共享。

实现要点（踩过的坑）：父类 ``SessionMemory.session()`` 用 ``setdefault`` 返回
**同一个 dict**，调用方（父类的 observe/remember_turn）直接原地改它。所以这里
``session()`` 必须返回**同一个缓存对象**并把 Redis 当持久层，写完再 flush；
如果每次 ``session()`` 都从 Redis 反序列化一个新 dict，父类的写入会落在临时对象上，
数据永远存不进去。
"""
from __future__ import annotations

import json
import os

from app.memory.session import SessionMemory

_EMPTY: dict = {"history": [], "slots": {}, "seen": {}}


def redis_url() -> str:
    return os.environ.get("REDIS_URL", "redis://127.0.0.1:6399/0")


class RedisSessionMemory(SessionMemory):
    """用 Redis String(key) 存整个会话 JSON（key: ``agent:session:<id>``），带 TTL。"""

    def __init__(self, client=None, ttl_seconds: int = 3600):
        super().__init__()                      # 初始化 _sessions 缓存
        if client is not None:
            self._redis = client
        else:
            try:
                import redis                     # 惰性导入
            except ImportError as exc:           # pragma: no cover
                raise RuntimeError(
                    "Redis 支持需要 redis-py：pip install -r requirements-db.txt"
                ) from exc
            self._redis = redis.Redis.from_url(redis_url(), decode_responses=True)
        self._ttl = ttl_seconds

    # ---- 存储层 --------------------------------------------------------
    def _key(self, session_id: str) -> str:
        return f"agent:session:{session_id}"

    def session(self, session_id: str) -> dict:
        """返回**同一个**内存对象；首次访问时从 Redis 载入。"""
        cached = self._sessions.get(session_id)
        if cached is None:
            raw = self._redis.get(self._key(session_id))
            try:
                cached = json.loads(raw) if raw else json.loads(json.dumps(_EMPTY))
            except (TypeError, json.JSONDecodeError):
                cached = json.loads(json.dumps(_EMPTY))
            self._sessions[session_id] = cached
        return cached

    def flush(self, session_id: str) -> None:
        payload = json.dumps(self.session(session_id), ensure_ascii=False)
        self._redis.set(self._key(session_id), payload, ex=self._ttl)

    # ---- 写操作：先走父类逻辑，再落 Redis --------------------------------
    def observe(self, session_id: str, tool: str, data: dict | None) -> None:
        super().observe(session_id, tool, data)
        self.flush(session_id)

    def note(self, session_id: str, key: str, value) -> None:
        super().note(session_id, key, value)
        self.flush(session_id)

    def remember_turn(self, session_id: str, user_input: str, answer: str,
                      intent: str | None = None) -> None:
        super().remember_turn(session_id, user_input, answer, intent)
        self.flush(session_id)
