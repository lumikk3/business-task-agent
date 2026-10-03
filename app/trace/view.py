"""Trace 视图（Step 13）：把一条 Agent 执行链路渲染成树。

    T20261003ABC123   (total 42.3ms)
    ├── user_input
    ├── intent
    ├── plan
    ├── decision
    ├── permission
    ├── tool  query_order  (1.2ms, retry=0)
    ├── ...
    └── final
"""
from __future__ import annotations


def _label(span: dict) -> str:
    node = span.get("node", "?")
    bits = [node]
    if span.get("tool"):
        bits.append(span["tool"])
    detail = ""
    if node == "permission":
        out = span.get("output")
        if isinstance(out, dict) and out.get("mode"):
            detail = f"mode={out['mode']}"
    elif node == "decision":
        out = span.get("output")
        if isinstance(out, dict):
            detail = out.get("tool") or out.get("kind", "")
    elif node == "llm":
        inp = span.get("input")
        model = inp.get("model") if isinstance(inp, dict) else ""
        detail = f"model={model} tokens={span.get('tokens', 0)}"
    elif node == "tool":
        detail = f"{span.get('latency_ms', 0)}ms, retry={span.get('retry_count', 0)}"
    elif node in ("intent", "final"):
        detail = str(span.get("output") or "")[:40]
    if detail:
        bits.append(f"({detail})")
    if span.get("error"):
        bits.append(f"ERROR={span['error']}")
    return "  ".join(bits)


def render_tree(trace: dict) -> str:
    spans = trace.get("spans") or []
    header = f"{trace.get('trace_id', '?')}   (total {trace.get('total_latency_ms', 0)}ms)"
    lines = [header]
    for index, span in enumerate(spans):
        branch = "└──" if index == len(spans) - 1 else "├──"
        lines.append(f"{branch} {_label(span)}")
    return "\n".join(lines)


def load_trace_line(path, index: int = -1) -> dict:
    """从 JSONL 里读第 index 条 trace（默认最后一条）。"""
    import json
    from pathlib import Path

    lines = Path(path).read_text(encoding="utf-8").strip().splitlines()
    return json.loads(lines[index])


def render_last(path, index: int = -1) -> str:
    return render_tree(load_trace_line(path, index))
