#!/usr/bin/env python3
"""Atlas 迁移运行器 CLI（docs/30 §3，ADR T24）。

用法：
    DATABASE_URL=... python -m scripts.ops.apply_migrations [--mark-existing]

按文件名顺序应用 db/migrations/*.sql 中未登记的迁移；失败即回滚并以非零码退出。
"""

from __future__ import annotations

import argparse
import sys

from atlas.memory.database import create_database_engine, wait_for_database
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
    # docs/79（打包 S）D-4：空卷首启的 PG recovery 窗口有界重试，重试的是整个
    # apply_pending（连接可能落在任何一次 engine.begin() 上；每次入口重算已应用版本，
    # 单事务执行 ⇒ 重试幂等安全）。预算耗尽仍抛 ⇒ 非零码退出，fail-closed 不变。
    processed = wait_for_database(
        lambda: apply_pending(
            engine,
            default_migrations_dir(),
            mark_existing=args.mark_existing,
        )
    )
    if processed:
        for version in processed:
            print(f"applied {version}")
    else:
        print("no pending migrations")
    return 0


if __name__ == "__main__":
    sys.exit(main())
