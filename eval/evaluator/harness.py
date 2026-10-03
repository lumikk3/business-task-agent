"""评测执行器（Step 14）：把 Case 逐个跑过 Agent,算出指标并落报告。

跑的是**真实**的 Agent（完整版 AgentRuntime），指标全部来自这次运行,不是编的。
默认用确定性 RuleBasedBrain（离线、快、可复现）；也支持 --brain llm 跑真 LLM 子集。

为什么从 Trace 取工具链而不是结果对象：Trace 记录的是**实际执行过**的每一次工具调用
（含失败的），用它判断「有没有调用被禁止的工具」更准确。
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from app.agent.brain import RuleBasedBrain
from app.agent.runtime import AgentRuntime
from app.data.generator import DEFAULT_DB_PATH, generate
from app.memory.context import MemoryStore
from app.rag.policy_rag import PolicyRAG
from app.store import open_scratch_store
from app.tools.business import BusinessTools, build_registry
from app.tools.faults import inject_faults
from app.tools.registry import ToolExecutor

REPORTS_DIR = Path(__file__).resolve().parents[1] / "reports"

REQUIRED_ARGS = {
    "query_order": ["user_id"],
    "query_logistics": ["order_id"],
    "search_after_sales_policy": ["query"],
    "create_return_request": ["order_id", "reason"],
    "create_refund_request": ["order_id", "amount"],
    "create_human_ticket": ["user_id", "reason"],
}


def build_runtime(db_path, brain, fault_timeout=0.0, fault_error=0.0, max_steps=8,
                  trace_dir=None):
    store = open_scratch_store(str(db_path))
    tools = BusinessTools(store, policy_search=PolicyRAG().retrieve)
    registry = build_registry(tools)
    if fault_timeout or fault_error:
        registry = inject_faults(registry, timeout_rate=fault_timeout,
                                 error_rate=fault_error, tools=("query_order",))
    executor = ToolExecutor(registry, max_retries=2, timeout_s=3.0, sleep=lambda _s: None)
    return AgentRuntime(brain, registry, executor, MemoryStore(),
                        max_steps=max_steps, trace_dir=trace_dir)


def _tool_calls_from_trace(trace: dict) -> list[dict]:
    return [{"tool": span.get("tool"), "arguments": span.get("arguments") or {},
             "ok": span.get("error") is None}
            for span in trace.get("spans", []) if span.get("node") == "tool"]


def _args_ok(case: dict, tool_calls: list[dict]) -> bool:
    for call in tool_calls:
        name = call["tool"]
        arguments = call["arguments"]
        for key in REQUIRED_ARGS.get(name, []):
            if arguments.get(key) in (None, ""):
                return False
        if name == "query_order" and arguments.get("user_id") != case["user_id"]:
            return False
        if name == "create_return_request" and case.get("expected_order_id"):
            if arguments.get("order_id") != case["expected_order_id"]:
                return False
    return True


def run_case(runtime, case: dict, trace_file: Path) -> dict:
    start = time.perf_counter()
    result = runtime.run(case["input"], user_id=case["user_id"],
                         session_id=f"eval-{case['id']}")
    wall_ms = (time.perf_counter() - start) * 1000

    trace = {}
    if trace_file.exists():
        lines = trace_file.read_text(encoding="utf-8").strip().splitlines()
        if lines:
            trace = json.loads(lines[-1])
    tool_calls = _tool_calls_from_trace(trace)
    actual_tools = [call["tool"] for call in tool_calls]

    ok_intent = result.intent == case["expected_intent"]
    ok_tools = sorted(actual_tools) == sorted(case["expected_tools"])
    ok_args = _args_ok(case, tool_calls)
    ok_status = result.status == case["expected_status"]
    ok_policy = not (set(actual_tools) & set(case.get("forbidden_tools", [])))

    return {
        "id": case["id"],
        "input": case["input"],
        "user_id": case["user_id"],
        "expected_intent": case["expected_intent"],
        "actual_intent": result.intent,
        "expected_tools": case["expected_tools"],
        "actual_tools": actual_tools,
        "expected_status": case["expected_status"],
        "actual_status": result.status,
        "answer": result.final_response,
        "trace_id": result.trace_id,
        "ok_intent": ok_intent,
        "ok_tools": ok_tools,
        "ok_args": ok_args,
        "ok_status": ok_status,
        "ok_policy": ok_policy,
        "passed": all((ok_intent, ok_tools, ok_args, ok_status, ok_policy)),
        "latency_ms": round(wall_ms, 2),
        "tokens": result.tokens,
    }


def compute_metrics(records: list[dict]) -> dict:
    total = len(records) or 1
    return {
        "total": len(records),
        "intent_accuracy": round(sum(r["ok_intent"] for r in records) / total, 4),
        "tool_selection_accuracy": round(sum(r["ok_tools"] for r in records) / total, 4),
        "tool_argument_accuracy": round(sum(r["ok_args"] for r in records) / total, 4),
        "task_success_rate": round(sum(r["ok_status"] for r in records) / total, 4),
        "policy_compliance_rate": round(sum(r["ok_policy"] for r in records) / total, 4),
        "overall_pass_rate": round(sum(r["passed"] for r in records) / total, 4),
        "avg_latency_ms": round(sum(r["latency_ms"] for r in records) / total, 2),
        "total_tokens": sum(r["tokens"] for r in records),
    }


def run_eval(cases: list[dict], label: str = "rule", limit: int | None = None,
             db_path=DEFAULT_DB_PATH, brain=None, fault_timeout: float = 0.0,
             fault_error: float = 0.0, run_id: str | None = None) -> dict:
    if not Path(db_path).exists():
        generate(db_path)
    selected = cases[:limit] if limit else cases
    run_id = run_id or datetime.now(timezone.utc).strftime("run-%Y%m%d-%H%M%S")

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        trace_file = Path(tmp) / "agent_traces.jsonl"
        runtime = build_runtime(db_path, brain or RuleBasedBrain(),
                                fault_timeout=fault_timeout, fault_error=fault_error,
                                trace_dir=tmp)
        records = [run_case(runtime, case, trace_file) for case in selected]

    return {
        "run_id": run_id,
        "label": label,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fault_timeout": fault_timeout,
        "fault_error": fault_error,
        "metrics": compute_metrics(records),
        "cases": records,
    }


def write_report(report: dict, reports_dir: Path | str = REPORTS_DIR) -> tuple[Path, Path]:
    reports_dir = Path(reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    json_path = reports_dir / f"{report['run_id']}.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path = reports_dir / f"{report['run_id']}.md"
    md_path.write_text(format_report(report), encoding="utf-8")
    return json_path, md_path


def format_report(report: dict) -> str:
    metrics = report["metrics"]
    lines = [
        f"# Eval Report — {report['run_id']}",
        "",
        f"- label: `{report['label']}`",
        f"- created_at: {report['created_at']}",
        f"- cases: {metrics['total']}",
        "",
        "| 指标 | 数值 |",
        "| --- | --- |",
        f"| Intent Accuracy | {metrics['intent_accuracy']:.1%} |",
        f"| Tool Selection | {metrics['tool_selection_accuracy']:.1%} |",
        f"| Tool Arguments | {metrics['tool_argument_accuracy']:.1%} |",
        f"| Task Success | {metrics['task_success_rate']:.1%} |",
        f"| Policy Compliance | {metrics['policy_compliance_rate']:.1%} |",
        f"| Overall Pass | {metrics['overall_pass_rate']:.1%} |",
        f"| Avg Latency | {metrics['avg_latency_ms']} ms |",
        f"| Total Tokens | {metrics['total_tokens']} |",
        "",
    ]
    failed = [r for r in report["cases"] if not r["passed"]]
    lines.append(f"失败用例: {len(failed)}")
    for record in failed[:20]:
        lines.append(f"- {record['id']} `{record['input']}` "
                     f"expected={record['expected_intent']}/{record['expected_tools']} "
                     f"actual={record['actual_intent']}/{record['actual_tools']}")
    return "\n".join(lines) + "\n"


def load_report(path: Path | str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))
