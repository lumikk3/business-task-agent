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
from app.backend import (backend_names, ensure_sqlite_seed, open_business_store,
                         open_session_memory)
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
        self._mode = ""

    def _ensure(self) -> None:
        if self._minimal is not None or self._runtime is not None:
            return
        # 有 DATABASE_URL 走 PostgreSQL，否则走 SQLite 临时副本（Step 18）
        ensure_sqlite_seed()
        store = open_business_store(scratch=True)
        tools = BusinessTools(store, policy_search=PolicyRAG().retrieve)
        if has_llm_credentials():
            registry = build_registry(tools)          # 完整 6 工具,权限门才有意义
            executor = ToolExecutor(registry, max_retries=2, timeout_s=5.0)
            self._minimal = MinimalAgent(
                ChatClient(resolve_llm_config()), registry, executor,
                memory=open_session_memory(),         # 有 REDIS_URL 走 Redis
                permission_policy=PermissionPolicy(), trace_dir=_trace_dir())
            self._mode = "llm"
        else:
            registry = build_registry(tools)
            executor = ToolExecutor(registry, max_retries=2, timeout_s=5.0)
            self._runtime = AgentRuntime(RuleBasedBrain(), registry, executor,
                                         MemoryStore(), trace_dir=_trace_dir())
            self._mode = "rule"

    # ------------------------------------------------------------------
    def chat(self, user_id: str, message: str, session_id: str) -> dict:
        with self._lock:
            self._ensure()
            if self._minimal is not None:
                result = self._minimal.run(message, user_id=user_id, session_id=session_id)
                payload = {
                    "task_id": result.trace_id,
                    "trace_id": result.trace_id,
                    "session_id": session_id,
                    "status": result.status,
                    "answer": result.answer,
                    "tools": [c.name for c in result.tool_calls],
                    "pending": result.pending,
                }
            else:
                result = self._runtime.run(message, user_id=user_id, session_id=session_id)
                payload = {
                    "task_id": result.task_id,
                    "trace_id": result.trace_id,
                    "session_id": session_id,
                    "status": result.status,
                    "answer": result.final_response,
                    "tools": [c["tool"] for c in result.tool_calls],
                    "pending": result.pending,
                }
            payload["mode"] = self._mode
            self._tasks[payload["task_id"]] = payload
            self._traces[payload["trace_id"]] = _last_trace()
            return payload

    def confirm(self, session_id: str, confirm: bool) -> dict:
        with self._lock:
            if self._minimal is None:
                raise HTTPException(400, "confirm is only available in llm mode")
            try:
                result = self._minimal.resume(session_id, confirm=confirm)
            except ValueError as exc:
                raise HTTPException(404, str(exc)) from exc
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
    payload = {"status": "ok", "mode": SERVICE._mode or "uninitialized"}
    payload.update(backend_names())          # business_store / session_memory
    return payload


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    """极简客服工作台（直接调 /api/chat，可看到权限确认流程）。"""
    return (Path(__file__).resolve().parent / "static" / "index.html").read_text(
        encoding="utf-8")


@app.post("/api/chat")
def chat(request: ChatRequest) -> dict:
    return SERVICE.chat(request.user_id, request.message, request.session_id)


@app.post("/api/confirm")
def confirm(request: ConfirmRequest) -> dict:
    return SERVICE.confirm(request.session_id, request.confirm)


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
