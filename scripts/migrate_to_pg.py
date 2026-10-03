#!/usr/bin/env python3
"""Step 18：把「假企业业务系统」（SQLite）迁到 PostgreSQL。

幂等：默认先 TRUNCATE 再灌，可反复执行。

    python scripts/migrate_to_pg.py --dry-run          # 只看会写入什么
    DATABASE_URL=postgresql://hermes@127.0.0.1:55432/business \
        python scripts/migrate_to_pg.py

配合 scripts/dev_services.sh 免 root 起的本地 PostgreSQL 使用。
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.data.generator import DEFAULT_DB_PATH  # noqa: E402
from app.store_pg import TABLES, PgStore, dsn  # noqa: E402


def read_sqlite(path: str) -> dict[str, list[tuple]]:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    data: dict[str, list[tuple]] = {}
    try:
        for table, columns in TABLES.items():
            rows = conn.execute(f"SELECT {', '.join(columns)} FROM {table}").fetchall()
            data[table] = [tuple(row) for row in rows]
    finally:
        conn.close()
    return data


def dry_run(data: dict[str, list[tuple]]) -> None:
    print("SQLite 源数据：")
    for table, rows in data.items():
        print(f"  {table:22} {len(rows):>6} 行")
    print("\n将执行的 DDL（建表）：")
    print("  CREATE TABLE IF NOT EXISTS ...  —  共 12 张表（见 app/store_pg.py::DDL）")
    print("\n将执行的写入（示例）：")
    for table, columns in TABLES.items():
        if data[table]:
            print(f"  INSERT INTO {table} ({', '.join(columns)}) VALUES %s   "
                  f"x{len(data[table])}")


def migrate(data: dict[str, list[tuple]], dsn_str: str, truncate: bool = True) -> dict:
    import psycopg

    with psycopg.connect(dsn_str, autocommit=False) as conn:
        store = PgStore(conn, init=True)          # 建表（IF NOT EXISTS）
        with conn.cursor() as cur:
            if truncate:
                cur.execute(
                    "TRUNCATE " + ", ".join(TABLES) + " RESTART IDENTITY CASCADE")
            for table, columns in TABLES.items():
                rows = data[table]
                if not rows:
                    continue
                placeholders = ", ".join(["%s"] * len(columns))
                cur.executemany(
                    f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
                    rows)
        conn.commit()
        return store.counts()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite 源库")
    parser.add_argument("--dsn", default=None, help="目标 PostgreSQL（默认取 DATABASE_URL）")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-truncate", action="store_true",
                        help="不先清空目标表（追加模式）")
    args = parser.parse_args()

    data = read_sqlite(args.db)
    total = sum(len(rows) for rows in data.values())

    if args.dry_run:
        dry_run(data)
        print(f"\n共 {total} 行；正式执行请去掉 --dry-run")
        return

    counts = migrate(data, args.dsn or dsn(), truncate=not args.no_truncate)
    print(f"迁移完成: {args.db} -> {args.dsn or dsn()}")
    for table, count in counts.items():
        print(f"  {table:22} {count:>6} 行")
    print(f"合计 {sum(counts.values())} 行")


if __name__ == "__main__":
    main()
