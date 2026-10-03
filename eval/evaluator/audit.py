"""Bad Case 与 Regression（Step 15 / 16）。

* 从评测报告里挑出失败用例,归类错误类型并给出根因提示,落 bad_cases.jsonl。
* 对比两次评测报告：谁从 pass 变 fail（回归）、谁从 fail 变 pass（修好）。
"""
from __future__ import annotations

import json
from pathlib import Path

BAD_CASES_PATH = Path(__file__).resolve().parents[1] / "bad_cases" / "bad_cases.jsonl"


def classify(record: dict) -> tuple[str, str]:
    if not record["ok_intent"]:
        return ("Intent Classification Error",
                "意图没识别对:检查该意图的关键词/正则或分类 prompt")
    if not record["ok_tools"]:
        return ("Tool Selection Error",
                "工具选错:确认工具描述能区分相近动作(如 return vs refund),必要时加 few-shot")
    if not record["ok_args"]:
        return ("Tool Argument Error",
                "参数错误:检查工具入参是否被正确抽取与传递")
    if not record["ok_status"]:
        return ("Task Execution Error",
                "任务未达成期望状态:检查业务规则/权限门/兜底逻辑")
    if not record["ok_policy"]:
        return ("Policy Violation",
                "越权调用:调用了被禁止的高风险工具,检查权限策略")
    return ("Unknown", "")


def extract_bad_cases(report: dict) -> list[dict]:
    bad: list[dict] = []
    for record in report["cases"]:
        if record["passed"]:
            continue
        error_type, root_cause = classify(record)
        bad.append({
            "case_id": record["id"],
            "run_id": report["run_id"],
            "input": record["input"],
            "expected": {"intent": record["expected_intent"],
                         "tools": record["expected_tools"],
                         "status": record["expected_status"]},
            "actual": {"intent": record["actual_intent"],
                       "tools": record["actual_tools"],
                       "status": record["actual_status"]},
            "error_type": error_type,
            "root_cause": root_cause,
            "trace_id": record.get("trace_id"),
            "status": "open",
        })
    return bad


def write_bad_cases(bad_cases: list[dict], path: Path | str = BAD_CASES_PATH) -> int:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for case in bad_cases:
            handle.write(json.dumps(case, ensure_ascii=False) + "\n")
    return len(bad_cases)


def load_bad_cases(path: Path | str = BAD_CASES_PATH) -> list[dict]:
    path = Path(path)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def mark_fixed(case_id: str, path: Path | str = BAD_CASES_PATH) -> dict | None:
    """把某个 Bad Case 标记为 fixed（修好并复测通过后调用）。"""
    path = Path(path)
    cases = load_bad_cases(path)
    updated = None
    for case in cases:
        if case["case_id"] == case_id:
            case["status"] = "fixed"
            updated = case
    write_bad_cases(cases, path)
    return updated


def regression(baseline: dict, candidate: dict) -> dict:
    base = {r["id"]: r for r in baseline["cases"]}
    cand = {r["id"]: r for r in candidate["cases"]}
    shared = sorted(set(base) & set(cand))

    newly_failing = [cid for cid in shared if base[cid]["passed"] and not cand[cid]["passed"]]
    newly_passing = [cid for cid in shared if not base[cid]["passed"] and cand[cid]["passed"]]
    both_pass = [cid for cid in shared if base[cid]["passed"] and cand[cid]["passed"]]
    both_fail = [cid for cid in shared if not base[cid]["passed"] and not cand[cid]["passed"]]

    def delta(key: str) -> float:
        return round(candidate["metrics"][key] - baseline["metrics"][key], 4)

    return {
        "baseline": {"run_id": baseline["run_id"], "label": baseline["label"],
                     "passed": sum(r["passed"] for r in baseline["cases"])},
        "candidate": {"run_id": candidate["run_id"], "label": candidate["label"],
                      "passed": sum(r["passed"] for r in candidate["cases"])},
        "shared_cases": len(shared),
        "newly_failing": newly_failing,
        "newly_passing": newly_passing,
        "both_pass": len(both_pass),
        "both_fail": len(both_fail),
        "metric_delta": {key: delta(key) for key in
                         ("intent_accuracy", "tool_selection_accuracy",
                          "tool_argument_accuracy", "task_success_rate",
                          "policy_compliance_rate", "overall_pass_rate")},
    }


def format_regression(diff: dict) -> str:
    lines = [
        f"baseline : {diff['baseline']['label']}  passed={diff['baseline']['passed']}",
        f"candidate: {diff['candidate']['label']}  passed={diff['candidate']['passed']}",
        f"共同用例 : {diff['shared_cases']}",
        "",
        "指标变化:",
    ]
    for key, value in diff["metric_delta"].items():
        arrow = "▲" if value > 0 else ("▼" if value < 0 else "=")
        lines.append(f"  {key:26} {arrow} {value:+.1%}")
    lines += [
        "",
        f"新增回归 (pass -> fail): {len(diff['newly_failing'])} {diff['newly_failing'][:10]}",
        f"修好       (fail -> pass): {len(diff['newly_passing'])} {diff['newly_passing'][:10]}",
        f"一直通过: {diff['both_pass']}    一直失败: {diff['both_fail']}",
    ]
    if diff["newly_failing"]:
        lines.append("")
        lines.append("⚠ 只看总体通过率会漏掉回归 —— 上面这些用例是变差的,必须逐条看。")
    return "\n".join(lines)
