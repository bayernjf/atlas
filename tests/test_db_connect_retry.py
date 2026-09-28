# -*- coding: utf-8 -*-
"""docs/79（打包 S）守护：空卷首启 PG recovery 窗口的有界重试。

三组断言：
- helper 本身：重试后成功／预算耗尽 fail-closed／真错误不重试／文案分类；
- 迁移 CLI 接入点：首抛后重试成功、恒抛则 fail-closed（非零退出路径）；
- 应用 lifespan 接入点：连通等待**先于**恢复扫描，且预算耗尽**不阻断**启动。

反向面（不可省的判别对照）：U946 若把 `ProgrammingError` 也当可重试，会因"任何错误都
重试到预算耗尽"而空绿；U947 若不做认证/库不存在的否定判定，`connection failed` 这条
通用前缀会把配置错误误判成可重试。
"""
from __future__ import annotations

import asyncio
import os
import sys
import types

import pytest
from sqlalchemy.exc import OperationalError, ProgrammingError

from atlas.memory.database import (
    is_retryable_connectivity_error,
    wait_for_database,
)

RECOVERY_MSG = "FATAL: the database system is in recovery mode"


@pytest.fixture(autouse=True)
def _neutral_env():
    """每条用例自带档位与重试预算，不继承机器上残留的环境变量。"""
    saved = {k: os.environ.get(k) for k in ("ATLAS_ENV", "ATLAS_DB_READY_TIMEOUT_SECONDS",
                                            "ATLAS_DB_READY_INTERVAL_SECONDS")}
    os.environ["ATLAS_ENV"] = "dev"
    os.environ.pop("ATLAS_DB_READY_TIMEOUT_SECONDS", None)
    os.environ.pop("ATLAS_DB_READY_INTERVAL_SECONDS", None)
    yield
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def _operational(message: str = RECOVERY_MSG) -> OperationalError:
    return OperationalError("SELECT 1", {}, Exception(message))


def test_u944_wait_for_database_retries_then_succeeds():
    calls = {"n": 0}

    def operation():
        calls["n"] += 1
        if calls["n"] < 3:
            raise _operational()
        return "ok"

    assert wait_for_database(operation, timeout_seconds=1, interval_seconds=0) == "ok"
    assert calls["n"] == 3


def test_u945_wait_for_database_raises_when_budget_exhausted():
    calls = {"n": 0}

    def operation():
        calls["n"] += 1
        raise _operational()

    with pytest.raises(OperationalError):
        wait_for_database(operation, timeout_seconds=0.05, interval_seconds=0.01)
    # 有界：预算耗尽即抛，绝不无限重试
    assert 1 <= calls["n"] <= 50


def test_u946_wait_for_database_does_not_retry_real_errors():
    calls = {"n": 0}

    def operation():
        calls["n"] += 1
        raise ProgrammingError(
            "CREATE TABLE", {}, Exception('syntax error at or near "CREAT"')
        )

    with pytest.raises(ProgrammingError):
        wait_for_database(operation, timeout_seconds=5, interval_seconds=0)
    assert calls["n"] == 1


def test_u947_is_retryable_connectivity_error_classifies():
    assert is_retryable_connectivity_error(_operational("FATAL: the database system is in recovery mode"))
    assert is_retryable_connectivity_error(_operational("FATAL: the database system is starting up"))
    assert is_retryable_connectivity_error(_operational("connection refused"))
    # 认证失败/库不存在走同一个 "connection failed" 前缀，必须显式否定
    assert not is_retryable_connectivity_error(
        _operational('connection failed: FATAL:  password authentication failed for user "atlas"')
    )
    assert not is_retryable_connectivity_error(_operational('FATAL:  database "atlas" does not exist'))
    assert not is_retryable_connectivity_error(
        ProgrammingError("SELECT 1", {}, Exception("syntax error"))
    )
    assert not is_retryable_connectivity_error(ValueError("recovery mode"))


def test_u948_apply_migrations_retries_connectivity_then_applies(monkeypatch):
    from scripts.ops import apply_migrations

    monkeypatch.setenv("ATLAS_DB_READY_TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("ATLAS_DB_READY_INTERVAL_SECONDS", "0")
    monkeypatch.setattr(apply_migrations, "create_database_engine", lambda: object())
    monkeypatch.setattr(sys, "argv", ["apply_migrations"])
    calls = {"n": 0}

    def fake_apply_pending(engine, migrations_dir, *, mark_existing):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _operational()
        return ["001"]

    monkeypatch.setattr(apply_migrations, "apply_pending", fake_apply_pending)
    assert apply_migrations.main() == 0
    assert calls["n"] == 2


def test_u949_apply_migrations_fails_closed_when_db_never_ready(monkeypatch):
    from scripts.ops import apply_migrations

    monkeypatch.setenv("ATLAS_DB_READY_TIMEOUT_SECONDS", "0.05")
    monkeypatch.setenv("ATLAS_DB_READY_INTERVAL_SECONDS", "0.01")
    monkeypatch.setattr(apply_migrations, "create_database_engine", lambda: object())
    monkeypatch.setattr(sys, "argv", ["apply_migrations"])

    def always_fail(engine, migrations_dir, *, mark_existing):
        raise _operational()

    monkeypatch.setattr(apply_migrations, "apply_pending", always_fail)
    with pytest.raises(OperationalError):
        apply_migrations.main()


def test_u950_lifespan_waits_for_pg_before_recovery_scan(monkeypatch):
    import atlas.api.main as main

    monkeypatch.setattr(main, "STORAGE_BACKEND", "pg")
    monkeypatch.setenv("ATLAS_DB_READY_TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("ATLAS_DB_READY_INTERVAL_SECONDS", "0")
    monkeypatch.setattr(main, "get_pg_backend", lambda: types.SimpleNamespace(engine=object()))
    order: list[str] = []
    monkeypatch.setattr(main, "recover_pending", lambda: order.append("recover"))
    monkeypatch.setattr(main, "get_decision_client", lambda: order.append("decision"))
    monkeypatch.setattr(main, "run_retention_once", lambda: order.append("retention"))
    monkeypatch.setattr(main, "start_scheduler", lambda: order.append("start_scheduler"))
    monkeypatch.setattr(main, "stop_scheduler", lambda: order.append("stop_scheduler"))

    async def drive():
        async with main.lifespan(None):
            pass

    # 阶段 1：首连失败、第二次成功 ⇒ 恢复扫描必须排在连通之后
    pings = {"n": 0}

    def flaky_ping(engine):
        pings["n"] += 1
        order.append("ping")
        if pings["n"] == 1:
            raise _operational()
        return True

    monkeypatch.setattr(main, "ping", flaky_ping)
    asyncio.run(drive())
    assert order[:3] == ["ping", "ping", "recover"]
    assert order[3:] == ["decision", "retention", "start_scheduler", "stop_scheduler"]

    # 阶段 2：连通永远不通（预算耗尽）⇒ 只 warning，恢复扫描**仍被调用**（不阻断启动）
    order.clear()
    monkeypatch.setenv("ATLAS_DB_READY_TIMEOUT_SECONDS", "0")
    attempts = {"n": 0}

    def never_ready(engine):
        attempts["n"] += 1
        raise _operational()

    monkeypatch.setattr(main, "ping", never_ready)
    asyncio.run(drive())
    assert "recover" in order
    assert attempts["n"] == 1  # 预算 0 ⇒ 单次尝试即放弃