import time

import pytest

from app.tools.registry import (
    ErrorType,
    RiskLevel,
    ToolError,
    ToolExecutor,
    ToolRegistry,
    ToolSpec,
)


def _spec(name="demo", handler=None, required=("value",)):
    return ToolSpec(
        name=name,
        description="demo tool",
        parameters={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": list(required),
        },
        handler=handler or (lambda value: {"echo": value}),
        risk_level=RiskLevel.LOW,
    )


def test_register_get_and_openai_schema():
    registry = ToolRegistry()
    registry.register(_spec())
    assert registry.names() == ["demo"]
    schema = registry.schemas()[0]
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "demo"
    assert schema["function"]["parameters"]["required"] == ["value"]
    assert registry.risk_level("demo") == RiskLevel.LOW


def test_duplicate_and_unknown_tools():
    registry = ToolRegistry()
    registry.register(_spec())
    with pytest.raises(ToolError) as dup:
        registry.register(_spec())
    assert dup.value.error_type == ErrorType.VALIDATION
    with pytest.raises(ToolError) as missing:
        registry.get("nope")
    assert missing.value.error_type == ErrorType.NOT_FOUND


def test_executor_retries_retryable_errors_then_succeeds():
    calls = {"n": 0}

    def flaky(value):
        calls["n"] += 1
        if calls["n"] < 3:
            raise ToolError("temporary failure", ErrorType.EXECUTION, retryable=True)
        return {"echo": value}

    registry = ToolRegistry()
    registry.register(_spec(handler=flaky))
    executor = ToolExecutor(registry, max_retries=2, sleep=lambda _s: None)
    result = executor.execute("demo", {"value": "x"})
    assert result.ok is True
    assert result.attempts == 3
    assert calls["n"] == 3


def test_executor_gives_up_after_max_retries():
    def broken(value):
        raise ToolError("always down", ErrorType.EXECUTION, retryable=True)

    registry = ToolRegistry()
    registry.register(_spec(handler=broken))
    executor = ToolExecutor(registry, max_retries=2, sleep=lambda _s: None)
    result = executor.execute("demo", {"value": "x"})
    assert result.ok is False
    assert result.error_type == ErrorType.EXECUTION
    assert result.attempts == 3  # first try + 2 retries


def test_executor_timeout_is_classified_and_retried():
    def slow(value):
        time.sleep(0.3)
        return {"echo": value}

    registry = ToolRegistry()
    registry.register(_spec(handler=slow))
    executor = ToolExecutor(registry, max_retries=1, timeout_s=0.05,
                            sleep=lambda _s: None)
    result = executor.execute("demo", {"value": "x"})
    assert result.ok is False
    assert result.error_type == ErrorType.TIMEOUT
    assert result.attempts == 2


def test_missing_required_argument_is_validation_not_retried():
    calls = {"n": 0}

    def handler(value):
        calls["n"] += 1
        return {"echo": value}

    registry = ToolRegistry()
    registry.register(_spec(handler=handler))
    executor = ToolExecutor(registry, max_retries=2, sleep=lambda _s: None)
    result = executor.execute("demo", {})
    assert result.ok is False
    assert result.error_type == ErrorType.VALIDATION
    assert result.attempts == 1
    assert calls["n"] == 0


def test_unexpected_handler_crash_is_not_retried():
    calls = {"n": 0}

    def boom(value):
        calls["n"] += 1
        raise RuntimeError("bug in handler")

    registry = ToolRegistry()
    registry.register(_spec(handler=boom))
    executor = ToolExecutor(registry, max_retries=2, sleep=lambda _s: None)
    result = executor.execute("demo", {"value": "x"})
    assert result.ok is False
    assert result.error_type == ErrorType.EXECUTION
    assert "RuntimeError" in result.error
    assert result.attempts == 1
    assert calls["n"] == 1
