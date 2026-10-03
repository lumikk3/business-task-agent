#!/usr/bin/env python3
"""评测 CLI（Step 14 / 15 / 16）。

    # 基线评测（确定性 brain,离线,200 cases）
    python -m eval.evaluator.run_eval

    # 只跑前 20 条;用真 LLM
    python -m eval.evaluator.run_eval --limit 20 --brain llm

    # 复现一次「回归」并对比基线
    python -m eval.evaluator.run_eval --quality-window 1 \\
        --regression eval/reports/<baseline>.json

    # 查看 / 修复 Bad Case
    python -m eval.evaluator.run_eval --show-bad-cases
    python -m eval.evaluator.run_eval --mark-fixed case087
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.agent.brain import RuleBasedBrain  # noqa: E402
from app.data.generator import DEFAULT_DB_PATH  # noqa: E402
from eval.evaluator import audit, dataset, harness  # noqa: E402


def _make_brain(name: str, quality_window: int, no_reason_window: int):
    if name == "rule":
        return RuleBasedBrain(quality_window_days=quality_window,
                              no_reason_window_days=no_reason_window)
    if name == "llm":
        # 用 LLM 决策（OpenAIBrain 只需要工具 schema,registry 与 runtime 的一致）
        from app.agent.llm_brain import OpenAIBrain
        from app.rag.policy_rag import PolicyRAG
        from app.store import open_scratch_store
        from app.tools.business import BusinessTools, build_registry

        tools = BusinessTools(open_scratch_store(str(DEFAULT_DB_PATH)),
                              policy_search=PolicyRAG().retrieve)
        return OpenAIBrain(build_registry(tools))
    raise SystemExit(f"unknown brain: {name}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--brain", choices=("rule", "llm"), default="rule")
    parser.add_argument("--quality-window", type=int, default=15,
                        help="质量问题售后天数（调小可复现回归）")
    parser.add_argument("--no-reason-window", type=int, default=7)
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--out-dir", default=str(harness.REPORTS_DIR))
    parser.add_argument("--label", default=None)
    parser.add_argument("--regression", default=None, help="基线报告 json,与本次对比")
    parser.add_argument("--show-bad-cases", action="store_true")
    parser.add_argument("--mark-fixed", default=None, metavar="CASE_ID")
    args = parser.parse_args()

    if args.show_bad_cases:
        cases = audit.load_bad_cases()
        print(f"Bad Cases: {len(cases)}")
        for case in cases[:30]:
            print(f"  [{case['status']}] {case['case_id']} {case['error_type']}: "
                  f"{case['input']}")
            print(f"        expected={case['expected']} actual={case['actual']}")
        return

    if args.mark_fixed:
        updated = audit.mark_fixed(args.mark_fixed)
        print("已标记 fixed:" if updated else "未找到:", args.mark_fixed)
        return

    cases = dataset.load_cases()
    label = args.label or f"{args.brain}(quality={args.quality_window})"
    report = harness.run_eval(cases, label=label, limit=args.limit, db_path=args.db,
                              brain=_make_brain(args.brain, args.quality_window,
                                                args.no_reason_window))
    json_path, md_path = harness.write_report(report, args.out_dir)

    print(harness.format_report(report))
    print(f"报告: {json_path}")
    print(f"      {md_path}")

    bad = audit.extract_bad_cases(report)
    if bad:
        count = audit.write_bad_cases(bad)
        print(f"\nBad Cases 已写入 {count} 条 -> {audit.BAD_CASES_PATH}")
        for case in bad[:5]:
            print(f"  {case['case_id']}  {case['error_type']}  |  {case['root_cause']}")

    if args.regression:
        baseline = harness.load_report(args.regression)
        print()
        print(audit.format_regression(audit.regression(baseline, report)))


if __name__ == "__main__":
    main()
