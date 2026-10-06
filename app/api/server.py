"""把 Agent 做成后端服务（Step 17）。

    POST /api/chat              {user_id, message} -> {task_id, status, answer, tools}
    POST /api/confirm           {session_id, confirm} -> 处理权限门的二次确认
    GET  /api/tasks/{task_id}
    GET  /api/traces/{task_id}
    GET  /api/eval/runs
    GET  /api/bad-cases
    GET  /health

有 GLM_API_KEY 时走最小版 LLM Agent（含权限门）；没有则回退到确定性 Runtime，
所以本地/CI 都能起来。

    python -m uvicorn app.api.server:app --port 8077
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app.agent.brain import RuleBasedBrain
from app.agent.llm_client import ChatClient, has_llm_credentials, resolve_llm_config
from app.agent.minimal_agent import MinimalAgent
from app.agent.permissions import PermissionPolicy
from app.agent.runtime import AgentRuntime
from app.backend import (backend_names, database_url, ensure_sqlite_seed,
                         open_business_store, open_session_memory, redis_url)
from app.memory.context import MemoryStore
from app.rag.policy_rag import PolicyRAG
from app.tools.business import BusinessTools, build_registry
from app.tools.registry import ToolExecutor

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REPORTS_DIR = PROJECT_ROOT / "eval" / "reports"
BAD_CASES_PATH = PROJECT_ROOT / "eval" / "bad_cases" / "bad_cases.jsonl"


class ChatRequest(BaseModel):
    user_id: str = "U10001"
    message: str
    session_id: str = "api"


class ConfirmRequest(BaseModel):
    session_id: str
    confirm: bool = True


class AgentService:
    """进程内单例：持有 agent、任务与 trace 存储。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tasks: dict[str, dict] = {}
        self._traces: dict[str, dict] = {}
        self._minimal = None
        self._runtime = None
        self._store = None
        self._memory = None
        self._mode = ""
        # 数据库可用性熔断：None=未知（下一次请求探一次）,True=已知可用（不再探测）,
        # False=已知不可用（每个请求只做一次廉价探测,避免反复打挂在 LLM 上）
        self._db_ok: bool | None = None

    def _db_ready(self) -> bool:
        """数据库是否可用。**每次请求都做一次廉价预检**（新建短连接,健康时几毫秒）。

        为什么不缓存"上次可用"：数据库可能在上一次成功之后才挂掉，缓存会让这次
        请求直接进入 Agent 循环——工具在假死 socket 上卡住、被误判成"可重试超时"，
        最后把请求拖到客户端超时（实测 60s+）。宁可每次多花几毫秒连接开销。
        """
        ping = (getattr(self._store, "ping_resilient", None)
                or getattr(self._store, "ping", None))
        ok = bool(ping()) if callable(ping) else True
        self._db_ok = ok
        return ok

    def _ensure(self) -> None:
        if self._minimal is not None or self._runtime is not None:
            return
        # 有 DATABASE_URL 走 PostgreSQL，否则走 SQLite 临时副本（Step 18）
        ensure_sqlite_seed()
        store = open_business_store(scratch=True)
        self._store = store
        tools = BusinessTools(store, policy_search=PolicyRAG().retrieve)
        if has_llm_credentials():
            registry = build_registry(tools)          # 完整 6 工具,权限门才有意义
            executor = ToolExecutor(registry, max_retries=2, timeout_s=5.0)
            self._memory = open_session_memory()      # 有 REDIS_URL 走 Redis
            self._minimal = MinimalAgent(
                ChatClient(resolve_llm_config()), registry, executor,
                memory=self._memory,
                permission_policy=PermissionPolicy(), trace_dir=_trace_dir())
            self._mode = "llm"
        else:
            registry = build_registry(tools)
            executor = ToolExecutor(registry, max_retries=2, timeout_s=5.0)
            self._runtime = AgentRuntime(RuleBasedBrain(), registry, executor,
                                         MemoryStore(), trace_dir=_trace_dir())
            self._mode = "rule"

    # ------------------------------------------------------------------
    def checks(self) -> dict:
        """后端真实可达性（给 /health 用）。

        Agent 还没初始化时也直接探一次后端，这样 /health 从第一次调用起就有意义
        （而不是先返回 null，要等有人发过消息才知道数据库通不通）。
        """
        if self._store is None:
            return {"postgres": _probe_postgres(), "redis": _probe_redis()}
        result: dict = {}
        # PgStore 有 ping()；SQLite 版只要没抛异常就算通
        ping = getattr(self._store, "ping", None)
        try:
            result["postgres"] = bool(ping()) if callable(ping) else True
        except Exception:                             # noqa: BLE001
            result["postgres"] = False
        client = getattr(self._memory, "_redis", None)
        try:
            result["redis"] = bool(client.ping()) if client is not None else True
        except Exception:                             # noqa: BLE001
            result["redis"] = False
        return result

    # ------------------------------------------------------------------
    def chat(self, user_id: str, message: str, session_id: str) -> dict:
        with self._lock:
            self._ensure()
            if not self._db_ready():
                # 已知数据库不可用：不要再去打 LLM + 工具重试（会把请求拖到超时）
                raise ConnectionError("后端数据库不可用（已熔断，等待恢复）")
            try:
                payload = self._invoke(message, user_id, session_id)
            except ConnectionError:
                self._db_ok = False          # 打开熔断,后续请求快速失败
                raise
            self._db_ok = True
            payload["mode"] = self._mode
            self._tasks[payload["task_id"]] = payload
            self._traces[payload["trace_id"]] = _last_trace()
            return payload

    def _invoke(self, message: str, user_id: str, session_id: str) -> dict:
        """跑一次 Agent，并把两种结果形态归一化成同一个响应字典。"""
        if self._minimal is not None:
            result = self._minimal.run(message, user_id=user_id, session_id=session_id)
            return {
                "task_id": result.trace_id,
                "trace_id": result.trace_id,
                "session_id": session_id,
                "status": result.status,
                "answer": result.answer,
                "tools": [c.name for c in result.tool_calls],
                "pending": result.pending,
            }
        result = self._runtime.run(message, user_id=user_id, session_id=session_id)
        return {
            "task_id": result.task_id,
            "trace_id": result.trace_id,
            "session_id": session_id,
            "status": result.status,
            "answer": result.final_response,
            "tools": [c["tool"] for c in result.tool_calls],
            "pending": result.pending,
        }

    def confirm(self, session_id: str, confirm: bool) -> dict:
        with self._lock:
            if self._minimal is None:
                raise HTTPException(400, "confirm is only available in llm mode")
            if not self._db_ready():
                raise ConnectionError("后端数据库不可用（已熔断，等待恢复）")
            try:
                result = self._minimal.resume(session_id, confirm=confirm)
            except ValueError as exc:
                raise HTTPException(404, str(exc)) from exc
            except ConnectionError:
                self._db_ok = False
                raise
            self._db_ok = True
            payload = {
                "task_id": result.trace_id, "trace_id": result.trace_id,
                "session_id": session_id, "status": result.status,
                "answer": result.answer,
                "tools": [c.name for c in result.tool_calls],
                "pending": result.pending, "mode": self._mode,
            }
            self._tasks[payload["task_id"]] = payload
            self._traces[payload["trace_id"]] = _last_trace()
            return payload

    def task(self, task_id: str) -> dict | None:
        return self._tasks.get(task_id)

    def trace(self, task_id: str) -> dict | None:
        if task_id in self._traces:
            return self._traces[task_id]
        payload = self._tasks.get(task_id)
        if payload:
            return self._traces.get(payload.get("trace_id"))
        return None


def _probe_postgres() -> bool | None:
    """独立探一次 PostgreSQL 可达性（不建表）。未配置 PG 时返回 None。"""
    if not database_url():
        return None
    try:
        from app.store_pg import PgStore
        store = PgStore(init=False)          # 构造阶段就是一次快速连接尝试
        store.close()
        return True
    except Exception:                         # noqa: BLE001
        return False


def _probe_redis() -> bool | None:
    """独立探一次 Redis 可达性。未配置 Redis 时返回 None。"""
    if not redis_url():
        return None
    try:
        import redis as redis_lib
        client = redis_lib.Redis.from_url(redis_url(), socket_connect_timeout=1)
        return bool(client.ping())
    except Exception:                         # noqa: BLE001
        return False


def _trace_dir() -> Path:
    path = PROJECT_ROOT / "traces"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _last_trace() -> dict:
    path = _trace_dir() / "agent_traces.jsonl"
    if not path.exists():
        return {}
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    return json.loads(lines[-1]) if lines else {}


SERVICE = AgentService()
app = FastAPI(title="Business Task Agent API", version="1.0")


@app.get("/health")
def health() -> dict:
    """存活探针（永远 200）。``checks`` 反映后端真实可达性，便于观察重启后是否恢复。"""
    payload: dict = {"status": "ok", "mode": SERVICE._mode or "uninitialized"}
    payload.update(backend_names())          # business_store / session_memory
    payload["checks"] = SERVICE.checks()
    return payload


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    """极简客服工作台（直接调 /api/chat，可看到权限确认流程）。"""
    return (Path(__file__).resolve().parent / "static" / "index.html").read_text(
        encoding="utf-8")


@app.post("/api/chat")
def chat(request: ChatRequest) -> dict:
    try:
        return SERVICE.chat(request.user_id, request.message, request.session_id)
    except ConnectionError as exc:
        # 后端数据库不可用：给 503（而不是 500/裸异常），调用方可据此重试
        raise HTTPException(status_code=503, detail=f"后端数据库不可用: {exc}") from exc


@app.post("/api/confirm")
def confirm(request: ConfirmRequest) -> dict:
    try:
        return SERVICE.confirm(request.session_id, request.confirm)
    except ConnectionError as exc:
        raise HTTPException(status_code=503, detail=f"后端数据库不可用: {exc}") from exc


@app.get("/api/tasks/{task_id}")
def get_task(task_id: str) -> dict:
    payload = SERVICE.task(task_id)
    if payload is None:
        raise HTTPException(404, f"unknown task: {task_id}")
    return payload


@app.get("/api/traces/{task_id}")
def get_trace(task_id: str) -> dict:
    trace = SERVICE.trace(task_id)
    if trace is None:
        raise HTTPException(404, f"unknown trace for task: {task_id}")
    return trace


@app.get("/api/eval/runs")
def list_eval_runs() -> dict:
    runs = []
    for path in sorted(REPORTS_DIR.glob("*.json"), reverse=True):
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        runs.append({"run_id": report.get("run_id"), "label": report.get("label"),
                     "created_at": report.get("created_at"),
                     "metrics": report.get("metrics")})
    return {"count": len(runs), "runs": runs}


@app.get("/api/bad-cases")
def list_bad_cases() -> dict:
    if not BAD_CASES_PATH.exists():
        return {"count": 0, "cases": []}
    cases = [json.loads(line) for line in
             BAD_CASES_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    return {"count": len(cases), "cases": cases}
