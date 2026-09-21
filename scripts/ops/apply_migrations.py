#!/usr/bin/env python3
"""Atlas 迁移运行器 CLI（docs/30 §3，ADR T24）。

用法：
    DATABASE_URL=... python -m scripts.ops.apply_migrations [--mark-existing]

按文件名顺序应用 db/migrations/*.sql 中未登记的迁移；失败即回滚并以非零码退出。
"""

from __future__ import annotations

import argparse
import sys

from atlas.memory.database import create_database_engine
from atlas.storage.migrations import (
    apply_pending,
    default_migrations_dir,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply pending Atlas SQL migrations")
    parser.add_argument(
        "--mark-existing",
        action="store_true",
        help="register unapplied migration files without executing them",
    )
    args = parser.parse_args()

    engine = create_database_engine()
    processed = apply_pending(
        engine,
        default_migrations_dir(),
        mark_existing=args.mark_existing,
    )
    if processed:
        for version in processed:
            print(f"applied {version}")
    else:
        print("no pending migrations")
    return 0


if __name__ == "__main__":
    sys.exit(main())
