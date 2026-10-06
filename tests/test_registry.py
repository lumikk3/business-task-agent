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


def test_timeout_does_not_wait_for_hung_handler():
    """超时必须真的"超时"：不能因为等待卡死的 handler 线程而变成阻塞。

    真实场景：数据库 socket 假死时 handler 会卡住很久，如果退出线程池时
    wait=True，一次「5s 超时」实际会阻塞十几秒，请求被拖垮。
    """

    def stuck(value):
        time.sleep(2.0)          # 模拟 socket 假死
        return {"echo": value}

    registry = ToolRegistry()
    registry.register(_spec(handler=stuck))
    executor = ToolExecutor(registry, max_retries=0, timeout_s=0.05,
                            sleep=lambda _s: None)
    start = time.perf_counter()
    result = executor.execute("demo", {"value": "x"})
    elapsed = time.perf_counter() - start

    assert result.ok is False
    assert result.error_type == ErrorType.TIMEOUT
    assert elapsed < 1.0, f"超时后仍阻塞了 {elapsed:.2f}s（线程池 wait=True 的坑）"


def test_connection_error_propagates_instead_of_becoming_tool_result():
    """数据库/依赖不可达是"后端挂了",不是工具调用失败——必须抛给上层降级。"""

    def down(value):
        raise ConnectionError("PostgreSQL 不可用")

    registry = ToolRegistry()
    registry.register(_spec(handler=down))
    executor = ToolExecutor(registry, max_retries=2, sleep=lambda _s: None)

    with pytest.raises(ConnectionError):
        executor.execute("demo", {"value": "x"})
