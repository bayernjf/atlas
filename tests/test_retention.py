"""docs/65 K-A retention 测试：env 组装 / 启动钩子 / CLI（常跑，无 integration 依赖）。

PG 侧 prune_expired 语义测试见 tests/test_retention_pg_integration.py。
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

from atlas.iam import registry
from atlas.storage.retention import (
    DEFAULT_DAYS,
    assemble_cutoffs,
    run_retention_once,
)
from scripts.ops.run_retention import main as retention_cli_main


# ---------- env 组装 ----------

def test_assemble_cutoffs_defaults(monkeypatch) -> None:
    for env in ("ATLAS_RETENTION_DAYS", *[f"ATLAS_RETENTION_{t.upper()}_DAYS" for t in DEFAULT_DAYS]):
        monkeypatch.delenv(env, raising=False)
    cutoffs = assemble_cutoffs()
    now = datetime.now(timezone.utc)
    assert set(cutoffs) == set(DEFAULT_DAYS)
    for table, days in DEFAULT_DAYS.items():
        cutoff = datetime.fromisoformat(cutoffs[table])
        expected = now - timedelta(days=days)
        assert abs((cutoff - expected).total_seconds()) < 60


def test_assemble_cutoffs_env_override(monkeypatch) -> None:
    monkeypatch.setenv("ATLAS_RETENTION_AUDIT_DAYS", "7")
    monkeypatch.setenv("ATLAS_RETENTION_DAYS", "5")  # 全局缺省，只影响未单独设置的表
    cutoffs = assemble_cutoffs()
    assert datetime.fromisoformat(cutoffs["audit_events"]) > datetime.now(timezone.utc) - timedelta(days=8)
    assert datetime.fromisoformat(cutoffs["runs"]) > datetime.now(timezone.utc) - timedelta(days=6)


def test_assemble_cutoffs_invalid_env_falls_back(monkeypatch) -> None:
    monkeypatch.setenv("ATLAS_RETENTION_RUNS_DAYS", "not-a-number")
    monkeypatch.setenv("ATLAS_RETENTION_DAYS", "-3")  # 负数按 1 天计
    cutoffs = assemble_cutoffs()
    now = datetime.now(timezone.utc)
    # runs 非法 → 回退表默认 90 天
    assert abs((datetime.fromisoformat(cutoffs["runs"]) - (now - timedelta(days=90))).total_seconds()) < 60
    # audit 未单独设置 → 全局 -3 → max(1, -3) = 1 天
    assert abs((datetime.fromisoformat(cutoffs["audit_events"]) - (now - timedelta(days=1))).total_seconds()) < 60


# ---------- 启动钩子 ----------

def test_run_retention_once_memory_noop(monkeypatch) -> None:
    monkeypatch.setattr(registry, "STORAGE_BACKEND", "memory")
    assert run_retention_once() is None


# ---------- CLI ----------

def test_cli_memory_noop(monkeypatch, capsys) -> None:
    monkeypatch.setattr(registry, "STORAGE_BACKEND", "memory")
    monkeypatch.setattr(sys, "argv", ["run_retention"])
    assert retention_cli_main() == 0
    assert "no-op" in capsys.readouterr().out


def test_cli_dry_run(monkeypatch, capsys) -> None:
    monkeypatch.setattr(registry, "STORAGE_BACKEND", "pg")
    monkeypatch.setattr(sys, "argv", ["run_retention", "--dry-run"])
    assert retention_cli_main() == 0
    out = capsys.readouterr().out
    assert "dry-run: DELETE from audit_events" in out
    assert "dry-run: no rows deleted" in out
