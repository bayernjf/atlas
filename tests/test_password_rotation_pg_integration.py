# -*- coding: utf-8 -*-
"""打包 AV（docs/95）U1140／U1145：迁移 042 与两档一致性（真 PG）。

    DATABASE_URL=postgresql+psycopg://atlas:atlas@localhost:5433/atlas \\
    ATLAS_RUN_INTEGRATION=1 .venv/bin/pytest -m integration tests/test_password_rotation_pg_integration.py

为什么这两条要真 PG（U1141–U1144、U1146 在内存档就绿了）：docs/95 §1.2 的整个论点是
**标志来自用户行而不是内存会话**——PG 档 `iam_sessions` 只持久化
`(tenant_id, username, role, expires_at)`，重建出的 Principal 带不了这个标志。只有把行
真落到 PG、再用**新 engine／新 store 实例**读回来，才证得到"重启后仍然强制"。
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text

RUN_INTEGRATION = os.environ.get("ATLAS_RUN_INTEGRATION") == "1"
DATABASE_URL = os.environ.get("DATABASE_URL", "")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not RUN_INTEGRATION or not DATABASE_URL,
        reason="set ATLAS_RUN_INTEGRATION=1 and DATABASE_URL to run password rotation PG tests",
    ),
]

TENANT = "pgrotationtest"


def _new_engine():
    from atlas.memory.database import create_database_engine

    return create_database_engine(DATABASE_URL, pool_size=2)


@pytest.fixture(scope="module")
def engine():
    from atlas.storage.migrations import apply_pending

    eng = _new_engine()
    apply_pending(eng)
    yield eng
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM iam_users WHERE tenant_id = :t"), {"t": TENANT})
    eng.dispose()


@pytest.fixture(autouse=True)
def _prod_profile():
    saved = os.environ.get("ATLAS_ENV")
    os.environ["ATLAS_ENV"] = "prod"
    os.environ.pop("ATLAS_ENABLE_DEMO_MOCK", None)
    yield
    if saved is None:
        os.environ.pop("ATLAS_ENV", None)
    else:
        os.environ["ATLAS_ENV"] = saved


def _username() -> str:
    return f"pgav-{uuid.uuid4().hex[:12]}"


def _pg_store(eng):
    from atlas.storage.pg import PgBackend

    return PgBackend(eng).user_store()


def _principal(username: str):
    from atlas.iam.principals import Principal, Role

    return Principal(
        tenant_id=TENANT,
        tenant_name="PG 轮换位测试",
        username=username,
        display_name="AV",
        role=Role.ADMIN,
    )


def _create(store, username: str) -> None:
    """两档 create 签名同构（keyword-only），所以这里可以一份写法跑两档。"""
    from atlas.iam.principals import Role

    store.create(
        tenant_id=TENANT,
        username=username,
        password="Strongpass-1",
        display_name="AV",
        role=Role.ADMIN,
    )


# --- U1140 迁移 042 幂等、老行读作未轮换 -------------------------------------


def test_u1140_migration_042_is_idempotent_and_leaves_old_rows_null(engine) -> None:
    from atlas.storage.migrations import apply_pending

    # 先造一条"042 之前的行"：不带新列的 INSERT，模拟既有库里已存在的账号。
    legacy = _username()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO iam_users (tenant_id, username, password_hash, display_name, "
                "role, status, created_at, updated_at) VALUES "
                "(:t, :u, 'legacy-hash', '老账号', 'admin', 'active', "
                "'2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
            ),
            {"t": TENANT, "u": legacy},
        )

    apply_pending(engine)
    apply_pending(engine)  # 重复应用必须不炸（IF NOT EXISTS）

    with engine.connect() as conn:
        nullable = conn.execute(
            text(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_name = 'iam_users' AND column_name = 'password_rotated_at'"
            )
        ).scalar()
        value = conn.execute(
            text(
                "SELECT password_rotated_at FROM iam_users "
                "WHERE tenant_id = :t AND username = :u"
            ),
            {"t": TENANT, "u": legacy},
        ).scalar()

    assert nullable == "YES", "轮换位必须可空——NULL 才是「从未本人改过」的正身"
    assert value is None, "既有账号在 042 之后读作未轮换，而不是空串或报错"

    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM iam_users WHERE tenant_id = :t AND username = :u"),
            {"t": TENANT, "u": legacy},
        )


# --- U1145 两档逐键一致 ------------------------------------------------------


def test_u1145_pg_and_memory_tiers_answer_identically(engine, monkeypatch) -> None:
    from atlas.iam import deps
    from atlas.iam.accounts import UserStore

    pg = _pg_store(engine)
    mem = UserStore()
    username = _username()
    for store in (pg, mem):
        _create(store, username)

    def verdict(store) -> bool:
        monkeypatch.setattr(deps, "user_store", store)
        return deps.must_change_password(_principal(username))

    assert verdict(pg) is True, "别人代设的口令在两档都该要求改密"
    assert verdict(mem) is True

    for store in (pg, mem):
        store.set_password(TENANT, username, "Changedpass-3", by_owner=True)
    assert verdict(pg) is False
    assert verdict(mem) is False

    # 重启模拟：新 engine、新 store 实例读同一张表，答案仍应是"已轮换"。
    eng2 = _new_engine()
    try:
        fresh = _pg_store(eng2)
        assert fresh.get(TENANT, username).password_rotated_at is not None
        assert verdict(fresh) is False, (
            "PG 档若把标志塞进内存会话，这里就会翻成 True/False 不一致"
        )
    finally:
        eng2.dispose()

    # admin 重置在两档都把章擦回 NULL（docs/95 §2 的 D-1 订正）。
    for store in (pg, mem):
        store.set_password(TENANT, username, "Resetpass-2")
    assert verdict(pg) is True
    assert verdict(mem) is True


def test_u1145_pg_seed_plan_leaves_admins_unrotated(engine) -> None:
    """prod 播的是引导口令 ⇒ 播种出的 admin 天生未轮换（强制首登改密的起点）。"""
    from atlas.iam.passwords import verify_password
    from atlas.iam.principals import Role, TenantUser

    username = _username()
    pg = _pg_store(engine)
    pg.seed(
        [
            TenantUser(
                tenant_id=TENANT,
                username=username,
                password="bootstrap-from-env",
                display_name="AV 播种",
                role=Role.ADMIN,
            )
        ]
    )
    account = pg.get(TENANT, username)
    assert account.password_rotated_at is None
    # 比的是"能不能验过"，不是哈希串相等——scrypt 带随机盐，同口令两次结果不同。
    assert verify_password("bootstrap-from-env", account.password_hash)
    assert not verify_password("whatever-else", account.password_hash)

    assert pg.list(TENANT) and all(a.password_rotated_at is None for a in pg.list(TENANT))
