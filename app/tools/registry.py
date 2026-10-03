"""Tool Registry + Tool Executor.

The registry is the single place where business tools are declared (name,
JSON schema, handler, risk level). The executor wraps every call with
timeout / retry / exponential backoff / error classification so the agent
never assumes a tool call succeeds (DESIGN.md 5.9).
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ErrorType(str, Enum):
    TIMEOUT = "timeout"
    NOT_FOUND = "not_found"
    VALIDATION = "validation"
    EXECUTION = "execution"
    PERMISSION = "permission"


class ToolError(Exception):
    """Typed tool failure with a retryable flag for the executor."""

    def __init__(self, message: str, error_type: ErrorType = ErrorType.EXECUTION,
                 retryable: bool = True):
        super().__init__(message)
        self.error_type = error_type
        self.retryable = retryable


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict  # JSON schema of the arguments object
    handler: Callable[..., dict]
    risk_level: RiskLevel = RiskLevel.LOW

    def openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass
class ToolResult:
    tool: str
    ok: bool
    data: dict | None = None
    error: str | None = None
    error_type: ErrorType | None = None
    attempts: int = 1
    latency_ms: float = 0.0
    risk_level: RiskLevel = RiskLevel.LOW

    def to_dict(self) -> dict:
        return {
            "tool": self.tool,
            "ok": self.ok,
            "data": self.data,
            "error": self.error,
            "error_type": self.error_type.value if self.error_type else None,
            "attempts": self.attempts,
            "latency_ms": round(self.latency_ms, 2),
            "risk_level": self.risk_level.value,
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ToolError(f"tool already registered: {spec.name}",
                            ErrorType.VALIDATION, retryable=False)
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec:
        try:
            return self._tools[name]
        except KeyError:
            raise ToolError(f"unknown tool: {name}", ErrorType.NOT_FOUND,
                            retryable=False) from None

    def names(self) -> list[str]:
        return list(self._tools)

    def schemas(self) -> list[dict]:
        return [spec.openai_schema() for spec in self._tools.values()]

    def risk_level(self, name: str) -> RiskLevel:
        return self.get(name).risk_level


class ToolExecutor:
    """Executes tools with timeout + retry + exponential backoff."""

    def __init__(self, registry: ToolRegistry, max_retries: int = 2,
                 timeout_s: float = 5.0, backoff_base: float = 0.2,
                 backoff_max: float = 2.0, sleep: Callable[[float], None] = time.sleep):
        self.registry = registry
        self.max_retries = max_retries
        self.timeout_s = timeout_s
        self.backoff_base = backoff_base
        self.backoff_max = backoff_max
        self._sleep = sleep

    def execute(self, name: str, arguments: dict | None = None) -> ToolResult:
        arguments = arguments or {}
        spec = self.registry.get(name)
        start = time.monotonic()
        attempts = 0
        while True:
            attempts += 1
            try:
                data = self._call_with_timeout(spec, arguments)
                return ToolResult(
                    tool=name, ok=True, data=data, attempts=attempts,
                    latency_ms=(time.monotonic() - start) * 1000,
                    risk_level=spec.risk_level,
                )
            except ToolError as exc:
                if not exc.retryable or attempts > self.max_retries:
                    return ToolResult(
                        tool=name, ok=False, error=str(exc),
                        error_type=exc.error_type, attempts=attempts,
                        latency_ms=(time.monotonic() - start) * 1000,
                        risk_level=spec.risk_level,
                    )
                self._sleep(min(self.backoff_base * (2 ** (attempts - 1)), self.backoff_max))
            except Exception as exc:  # unexpected handler bug — do not retry
                return ToolResult(
                    tool=name, ok=False, error=f"{type(exc).__name__}: {exc}",
                    error_type=ErrorType.EXECUTION, attempts=attempts,
                    latency_ms=(time.monotonic() - start) * 1000,
                    risk_level=spec.risk_level,
                )

    def _call_with_timeout(self, spec: ToolSpec, arguments: dict) -> dict:
        self._validate(spec, arguments)
        try:
            kwargs = {k: arguments[k] for k in spec.parameters.get("properties", {})
                      if k in arguments}
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(spec.handler, **kwargs)
                return future.result(timeout=self.timeout_s)
        except FuturesTimeoutError:
            raise ToolError(f"tool timed out after {self.timeout_s}s: {spec.name}",
                            ErrorType.TIMEOUT, retryable=True) from None

    def _validate(self, spec: ToolSpec, arguments: dict) -> None:
        required = spec.parameters.get("required", [])
        missing = [key for key in required if key not in arguments]
        if missing:
            raise ToolError(f"missing required arguments for {spec.name}: {missing}",
                            ErrorType.VALIDATION, retryable=False)
