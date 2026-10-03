#!/usr/bin/env python3
"""准备业务数据：生成"假的企业业务系统"（SQLite）。

    python scripts/generate_data.py                 # -> data/business.db
    python scripts/generate_data.py --db /tmp/x.db --seed 1
    python scripts/generate_data.py --today 2026-09-27

不接任何真实电商接口。同一 seed 生成同一份数据，可安全重复执行（覆盖旧库）。
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.data.generator import DEFAULT_SEED, DEMO_TODAY, generate  # noqa: E402


def _distribution(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        print("订单状态分布:")
        for status, n in conn.execute(
                "SELECT status, COUNT(*) FROM orders GROUP BY status ORDER BY 2 DESC"):
            print(f"  {status:10} {n}")
        print("物流状态分布:")
        for status, n in conn.execute(
                "SELECT status, COUNT(*) FROM logistics GROUP BY status ORDER BY 2 DESC"):
            print(f"  {status:10} {n}")
        print("售后申请状态分布:")
        for status, n in conn.execute(
                "SELECT status, COUNT(*) FROM after_sale_requests GROUP BY status ORDER BY 2 DESC"):
            print(f"  {status:10} {n}")
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(ROOT / "data" / "business.db"),
                        help="输出 SQLite 路径 (默认 data/business.db)")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="随机种子")
    parser.add_argument("--today", default=DEMO_TODAY.isoformat(),
                        help="数据里的“今天” YYYY-MM-DD")
    parser.add_argument("--stats", action="store_true", help="打印分布统计")
    args = parser.parse_args()

    stats = generate(args.db, seed=args.seed, today=date.fromisoformat(args.today))

    print(f"已生成业务库: {stats['db_path']}")
    print(f"  seed={stats['seed']}  today={stats['today']}")
    for table, count in stats["counts"].items():
        print(f"  {table:20} {count}")
    if args.stats:
        print()
        _distribution(stats["db_path"])
    print("\n演示用户:")
    for uid, meta in stats["demo_users"].items():
        print(f"  {uid}  {meta['name']}  ->  {meta['question']}")


if __name__ == "__main__":
    main()
