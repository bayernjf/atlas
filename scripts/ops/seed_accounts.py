"""幂等账号 seeder（ADR T25，docs/31 §2.2）：entrypoint 迁移之后执行一次。"""

from __future__ import annotations


def main() -> None:
    import os

    if os.environ.get("ATLAS_STORAGE_BACKEND", "memory") != "pg":
        print("non-pg backend, seeding skipped")
        return
    from atlas.iam.principals import seed_plan_for_profile
    from atlas.storage.pg import get_pg_backend

    # 播种计划按档位定（docs/66）：prod 只播各租户 admin＋引导口令。
    inserted = get_pg_backend().user_store().seed(seed_plan_for_profile())
    print(f"seeded {inserted} accounts" if inserted else "seed accounts already present")


if __name__ == "__main__":
    main()
