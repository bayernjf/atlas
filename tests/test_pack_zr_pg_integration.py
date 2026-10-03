# -*- coding: utf-8 -*-
"""打包 ZR（P2 工程债）迁移 038 PG 集成测试（integration 标记）。

DATABASE_URL=postgresql+psycopg://atlas:atlas@localhost:5432/atlas \
ATLAS_RUN_INTEGRATION=1 .venv/bin/pytest -m integration tests/test_pack_zr_pg_integration.py

U1097：临时空库 apply 全部迁移 → interruptions.created_at 归队 TIMESTAMPTZ；二次 apply 幂等
（038 的 DO 判型不命中、索引 IF NOT EXISTS 原样存在）。
U1098：idx_interruptions_tenant_created 复合索引存在。
U1099b：旧库混合格式 TEXT 数据（CURRENT_TIMESTAMP 空格分隔文本 + ISO 串）应用 038 后
类型正确、ORDER BY created_at 为真时间序（TEXT 字典序在混合格式下会错的场景）。
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from atlas.storage.migrations import applied_versions, apply_pending, default_migrations_dir

RUN_INTEGRATION = os.environ.get("ATLAS_RUN_INTEGRATION") == "1"
DATABASE_URL = os.environ.get("DATABASE_URL", "")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not RUN_INTEGRATION or not DATABASE_URL,
        reason="set ATLAS_RUN_INTEGRATION=1 and DATABASE_URL to run pack ZR PG integration",
    ),
]


@pytest.fixture()
def temp_database_url():
    """独立临时库：CREATE DATABASE 需要 autocommit 连接；admin 用 DATABASE_URL 的当前库。"""
    base = make_url(DATABASE_URL)
    admin_engine = create_engine(base, isolation_level="AUTOCOMMIT")
    db = f"atlas_zr_test_{uuid.uuid4().hex[:8]}"
    with admin_engine.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{db}"'))
    url = base.set(database=db)
    try:
        yield url
    finally:
        # 先释放连接池、再 terminate 残留会话、最后 DROP：引擎持有连接会让 DROP 报 ObjectInUse。
        admin_engine.dispose()
        with create_engine(base, isolation_level="AUTOCOMMIT").connect() as conn:
            conn.execute(
                text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :db"),
                {"db": db},
            )
            conn.execute(text(f'DROP DATABASE IF EXISTS "{db}"'))


def _apply_all(engine) -> None:
    apply_pending(engine, migrations_dir=default_migrations_dir())


# ---------- U1097：迁移 038 型归队 + 幂等 ----------


def test_038_converts_created_at_to_timestamptz_idempotently(temp_database_url) -> None:
    engine = create_engine(temp_database_url)
    _apply_all(engine)

    with engine.connect() as conn:
        col = conn.execute(
            text(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_name = 'interruptions' AND column_name = 'created_at'"
            )
        ).scalar_one()
    assert col == "timestamp with time zone"

    # 二次 apply 幂等：038 判型不命中，不重跑转换、不报错。
    versions_before = applied_versions(engine)
    _apply_all(engine)
    assert applied_versions(engine) == versions_before


# ---------- U1098：复合索引存在 ----------


def test_038_creates_tenant_created_index(temp_database_url) -> None:
    engine = create_engine(temp_database_url)
    _apply_all(engine)

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE tablename = 'interruptions' AND indexname = 'idx_interruptions_tenant_created'"
            )
        ).all()
    assert len(rows) == 1
    assert "(tenant_id, created_at)" in rows[0][0]


# ---------- U1099b：混合格式旧数据转换 + 真时间序 ----------


def test_038_mixed_text_rows_convert_and_sort_by_real_time(temp_database_url) -> None:
    """旧库 TEXT 数据混排（CURRENT_TIMESTAMP 空格分隔文本 + ISO 串）转换后按真时间序。

    反向门：若 038 缺失，TEXT 字典序下 '2026-09-01 00:00:00+00'（空格 0x20）会排在
    '2026-09-02T00:00:00+00:00'（T 0x54）之后——时间序必须把 09-01 排前。
    """
    engine = create_engine(temp_database_url)
    _apply_all(engine)

    # 阶段一：模拟 038 之前的旧库形态——列改回 TEXT 并注入混排数据（独立事务，先提交释放锁）。
    with engine.begin() as conn:
        conn.execute(
            text("ALTER TABLE interruptions ALTER COLUMN created_at TYPE TEXT")
        )
        conn.execute(
            text(
                "INSERT INTO interruptions "
                "(resume_token, tenant_id, run_id, node_id, kind, payload, deadline_at, created_at) "
                "VALUES "
                "('tk_iso', 't1', 'r1', 'n1', 'wait', '{}', NULL, '2026-09-02T00:00:00+00:00'), "
                "('tk_db', 't1', 'r2', 'n2', 'wait', '{}', NULL, '2026-09-01 00:00:00+00')"
            )
        )
        col = conn.execute(
            text(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_name = 'interruptions' AND column_name = 'created_at'"
            )
        ).scalar_one()
        assert col == "text"

    # 阶段二：升级路径——旧库应用 038（判型命中 text → timestamptz，USING 就地转换存量行）。
    # 不用 apply_pending：038 已在上一次全量 apply 中登记，重跑不会触发——手动执行其 SQL 精确模拟「038 未应用的旧库升级」。
    # 迁移文件是多语句（DO 块含内部 `;`），text() 不可拆；走 psycopg 原生连接一次执行。
    # 注意必须在独立事务执行：ALTER TYPE 重写表需表级排他锁，上一阶段事务未提交会阻塞挂起。
    sql_038 = (
        Path(__file__).resolve().parents[1]
        / "db" / "migrations" / "038_interruptions_created_at_timestamptz.sql"
    ).read_text(encoding="utf-8")
    with engine.raw_connection() as raw:
        raw.cursor().execute(sql_038)
        raw.commit()  # psycopg3 连接默认非 autocommit：显式提交，否则退出即回滚。

    # 阶段三：验证——列已归队、行未丢、ORDER BY created_at 为真时间序（tk_db 的 09-01 排前）。
    with engine.connect() as conn:
        col = conn.execute(
            text(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_name = 'interruptions' AND column_name = 'created_at'"
            )
        ).scalar_one()
        assert col == "timestamp with time zone"
        rows = conn.execute(
            text(
                "SELECT resume_token, created_at::text FROM interruptions "
                "WHERE tenant_id = 't1' ORDER BY created_at"
            )
        ).all()
    assert [r[0] for r in rows] == ["tk_db", "tk_iso"]
    assert rows[0][1].startswith("2026-09-01")
    assert rows[1][1].startswith("2026-09-02")
