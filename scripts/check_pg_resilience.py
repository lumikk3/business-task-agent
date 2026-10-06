#!/usr/bin/env python3
"""Step 18 韧性验证：PostgreSQL 重启后，Agent 能否自动恢复数据库连接？

模拟运维现场：连接建立 → 重启数据库 → 立刻密集查询，看
  * 有多少次失败、报什么错（AdminShutdown / connection closed / connection refused）
  * 多久恢复

    # 对 Docker 里的 postgres（compose 映射 5433）
    python scripts/check_pg_resilience.py \
        --dsn postgresql://hermes:hermes@127.0.0.1:5433/business \
        --restart-cmd "docker compose restart postgres"

    # 对 scripts/dev_services.sh 起的本地实例
    eval "$(scripts/dev_services.sh env)"
    python scripts/check_pg_resilience.py --restart-cmd "scripts/dev_services.sh down && scripts/dev_services.sh up"

退出码：0 = 恢复成功；1 = 超时未恢复。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.store_pg import PgStore, dsn  # noqa: E402


def _restart(command: str) -> None:
    print(f"  $ {command}")
    proc = subprocess.run(command, shell=True, capture_output=True, text=True, cwd=ROOT)
    tail = (proc.stdout + proc.stderr).strip().splitlines()
    for line in tail[-3:]:
        print(f"    {line}")
    if proc.returncode != 0:
        print(f"    (restart 命令退出码 {proc.returncode}，继续观察)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", default=None, help="默认取 DATABASE_URL")
    parser.add_argument("--restart-cmd", default=None, help="重启数据库的命令")
    parser.add_argument("--user-id", default="U10003")
    parser.add_argument("--timeout", type=float, default=30.0, help="恢复超时(秒)")
    parser.add_argument("--interval", type=float, default=0.2)
    args = parser.parse_args()

    target = args.dsn or dsn()
    print(f"目标: {target}\n")

    store = PgStore(dsn_str=target, init=False)
    warm = store.find_orders(args.user_id)
    print(f"[ok] 初始查询成功: {len(warm)} 个订单")

    if args.restart_cmd:
        print("\n[restart] 重启数据库 ...")
        _restart(args.restart_cmd)

    print(f"\n[probe] 每 {args.interval}s 查询一次，最多等 {args.timeout}s ...")
    start = time.perf_counter()
    failures: list[tuple[float, str, str]] = []
    attempts = 0
    recovered_at: float | None = None

    while time.perf_counter() - start < args.timeout:
        attempts += 1
        elapsed = time.perf_counter() - start
        try:
            orders = store.find_orders(args.user_id)
            recovered_at = elapsed
            print(f"  #{attempts:<3} t={elapsed:5.2f}s  成功 ({len(orders)} 个订单)")
            break
        except Exception as exc:                       # noqa: BLE001 - 就是要看异常
            message = str(exc).strip().splitlines()[0][:110]
            failures.append((elapsed, type(exc).__name__, message))
            print(f"  #{attempts:<3} t={elapsed:5.2f}s  失败 {type(exc).__name__}: {message}")
        time.sleep(args.interval)

    print("\n================ 结果 ================")
    print(f"总尝试 {attempts} 次，失败 {len(failures)} 次")
    if failures:
        kinds: dict[str, int] = {}
        for _, name, _ in failures:
            kinds[name] = kinds.get(name, 0) + 1
        print("按异常类型: " + ", ".join(f"{k}×{v}" for k, v in kinds.items()))
        first_fail = failures[0]
        print(f"第一次失败: t={first_fail[0]:.2f}s {first_fail[1]}: {first_fail[2]}")
    if recovered_at is None:
        print("✗ 超时未恢复 —— 需要修连接重试")
        raise SystemExit(1)
    print(f"✓ 已自动恢复 (t={recovered_at:.2f}s)")


if __name__ == "__main__":
    main()
