"""Trace recording (DESIGN.md 5.11).

Every agent run produces a trace: ordered spans with node name, input/output,
tool call metadata, latency, error and retry count. Traces persist as JSONL so
the eval engine and bad-case analysis can replay the execution chain.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Span:
    node: str
    timestamp: float
    input: object = None
    output: object = None
    tool: str | None = None
    arguments: dict | None = None
    latency_ms: float = 0.0
    tokens: int = 0
    error: str | None = None
    retry_count: int = 0

    def to_dict(self) -> dict:
        return {
            "node": self.node,
            "timestamp": round(self.timestamp, 3),
            "input": self.input,
            "output": self.output,
            "tool": self.tool,
            "arguments": self.arguments,
            "latency_ms": round(self.latency_ms, 2),
            "tokens": self.tokens,
            "error": self.error,
            "retry_count": self.retry_count,
        }


@dataclass
class Tracer:
    trace_id: str = field(default_factory=lambda: f"T{time.strftime('%Y%m%d')}{uuid.uuid4().hex[:6].upper()}")
    spans: list[Span] = field(default_factory=list)
    _start: float = field(default_factory=time.monotonic, repr=False)

    def record(self, node: str, input: object = None, output: object = None, *,
               tool: str | None = None, arguments: dict | None = None,
               latency_ms: float = 0.0, tokens: int = 0, error: str | None = None,
               retry_count: int = 0) -> Span:
        span = Span(
            node=node, timestamp=time.time(), input=input, output=output,
            tool=tool, arguments=arguments, latency_ms=latency_ms, tokens=tokens,
            error=error, retry_count=retry_count,
        )
        self.spans.append(span)
        return span

    def to_dict(self) -> dict:
        return {
            "trace_id": self.trace_id,
            "total_latency_ms": round((time.monotonic() - self._start) * 1000, 2),
            "spans": [span.to_dict() for span in self.spans],
        }

    def save(self, path: Path | str) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(self.to_dict(), ensure_ascii=False) + "\n")
        return path
