"""Eval / Bad Case / Regression 测试（Step 14~16）。"""
from __future__ import annotations

from eval.evaluator import audit, dataset, harness


def _record(case_id: str, passed: bool, **overrides) -> dict:
    record = {
        "id": case_id, "input": "x", "user_id": "U10001",
        "expected_intent": "ORDER_QUERY", "actual_intent": "ORDER_QUERY",
        "expected_tools": ["query_order"], "actual_tools": ["query_order"],
        "expected_status": "completed", "actual_status": "completed",
        "ok_intent": True, "ok_tools": True, "ok_args": True,
        "ok_status": True, "ok_policy": True, "passed": passed,
        "latency_ms": 1.0, "tokens": 0, "trace_id": "T1",
    }
    record.update(overrides)
    return record


def _report(run_id: str, records: list[dict]) -> dict:
    return {"run_id": run_id, "label": run_id, "metrics": harness.compute_metrics(records),
            "cases": records}


# ---- 数据集 ------------------------------------------------------------
def test_dataset_has_expected_size_and_shape():
    cases = dataset.build_cases(target=200)
    assert len(cases) == 200
    required = {"id", "input", "user_id", "expected_intent", "expected_tools",
                "expected_status", "forbidden_tools"}
    assert all(required <= set(case) for case in cases)
    assert {case["id"] for case in cases} == {f"case{i:03d}" for i in range(1, 201)}
    # 七类意图都要覆盖到
    assert len({case["expected_intent"] for case in cases}) == 7


def test_dataset_is_deterministic():
    assert dataset.build_cases() == dataset.build_cases()


# ---- 评测器 ------------------------------------------------------------
def test_run_eval_produces_real_metrics():
    report = harness.run_eval(dataset.build_cases(), label="test", limit=20)
    metrics = report["metrics"]
    assert metrics["total"] == 20
    assert len(report["cases"]) == 20
    for key in ("intent_accuracy", "tool_selection_accuracy", "tool_argument_accuracy",
                "task_success_rate", "policy_compliance_rate", "overall_pass_rate"):
        assert 0.0 <= metrics[key] <= 1.0
    # 每条 case 都必须带真实 trace
    assert all(record["trace_id"] for record in report["cases"])


def test_metrics_count_correctly():
    records = [_record("c1", True), _record("c2", False, ok_tools=False),
               _record("c3", False, ok_policy=False)]
    metrics = harness.compute_metrics(records)
    assert metrics["total"] == 3
    assert metrics["intent_accuracy"] == 1.0
    assert metrics["tool_selection_accuracy"] == round(2 / 3, 4)
    assert metrics["policy_compliance_rate"] == round(2 / 3, 4)
    assert metrics["overall_pass_rate"] == round(1 / 3, 4)


# ---- Bad Case ----------------------------------------------------------
def test_bad_cases_are_classified_by_error_type():
    report = _report("r1", [
        _record("c1", False, ok_intent=False, actual_intent="UNKNOWN"),
        _record("c2", False, ok_tools=False, actual_tools=["create_refund_request"]),
        _record("c3", False, ok_args=False),
        _record("c4", False, ok_status=False, actual_status="escalated"),
        _record("c5", False, ok_policy=False, actual_tools=["create_refund_request"]),
    ])
    bad = {case["case_id"]: case["error_type"] for case in audit.extract_bad_cases(report)}
    assert bad == {
        "c1": "Intent Classification Error",
        "c2": "Tool Selection Error",
        "c3": "Tool Argument Error",
        "c4": "Task Execution Error",
        "c5": "Policy Violation",
    }
    assert all(case["root_cause"] for case in audit.extract_bad_cases(report))


def test_write_and_mark_fixed(tmp_path):
    path = tmp_path / "bad.jsonl"
    report = _report("r1", [_record("c1", False, ok_tools=False)])
    audit.write_bad_cases(audit.extract_bad_cases(report), path)
    assert [c["case_id"] for c in audit.load_bad_cases(path)] == ["c1"]
    audit.mark_fixed("c1", path)
    assert audit.load_bad_cases(path)[0]["status"] == "fixed"


# ---- Regression --------------------------------------------------------
def test_regression_detects_pass_to_fail():
    baseline = _report("v1", [_record("c1", True), _record("c2", True), _record("c3", False)])
    candidate = _report("v2", [_record("c1", True), _record("c2", False), _record("c3", True)])
    diff = audit.regression(baseline, candidate)
    assert diff["newly_failing"] == ["c2"]
    assert diff["newly_passing"] == ["c3"]
    assert diff["both_pass"] == 1
    assert diff["baseline"]["passed"] == 2 and diff["candidate"]["passed"] == 2
    assert "c2" in audit.format_regression(diff)


def test_regression_reports_metric_delta():
    baseline = _report("v1", [_record("c1", True), _record("c2", True)])
    candidate = _report("v2", [_record("c1", True), _record("c2", False, ok_intent=False)])
    diff = audit.regression(baseline, candidate)
    assert diff["metric_delta"]["overall_pass_rate"] == -0.5
    assert diff["metric_delta"]["intent_accuracy"] == -0.5
