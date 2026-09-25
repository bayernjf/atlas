#!/usr/bin/env python3
"""Atlas retention 运行器 CLI（docs/65 K-A）。

用法：
    ATLAS_STORAGE_BACKEND=pg DATABASE_URL=... python -m scripts.ops.run_retention [--dry-run]

读 ATLAS_RETENTION_<TABLE>_DAYS（缺省回退 ATLAS_RETENTION_DAYS）组装截止串，
调 PgBackend.prune_expired；--dry-run 只打印计划不执行；进程内档 no-op 退出 0。
与 apply_migrations.py 同款薄 CLI 风格。
"""

from __future__ import annotations

import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Atlas data retention pruning")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print planned deletions without executing",
    )
    args = parser.parse_args()

    from atlas.iam.registry import STORAGE_BACKEND
    from atlas.storage.retention import assemble_cutoffs

    cutoffs = assemble_cutoffs()
    if STORAGE_BACKEND != "pg":
        print(f"storage backend={STORAGE_BACKEND}: retention no-op (PG only)")
        return 0
    if args.dry_run:
        for table, cutoff in sorted(cutoffs.items()):
            print(f"dry-run: DELETE from {table} where < {cutoff}")
        print("dry-run: no rows deleted")
        return 0
    from atlas.storage.pg import get_pg_backend

    deleted = get_pg_backend().prune_expired(cutoffs)
    for table, count in sorted(deleted.items()):
        print(f"{table}: deleted {count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
