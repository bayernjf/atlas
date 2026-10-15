"""docs/65 K-A retention 测试：env 组装 / 启动钩子 / CLI（常跑，无 integration 依赖）。

PG 侧 prune_expired 语义测试见 tests/test_retention_pg_integration.py。
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

import pytest

from atlas.iam import registry
from atlas.storage.retention import (
    DEFAULT_DAYS,
    MEMORY_TABLE,
    assemble_cutoffs,
    run_retention_once,
)
from scripts.ops.run_retention import main as retention_cli_main


# ---------- env 组装 ----------

def test_assemble_cutoffs_defaults(monkeypatch) -> None:
    for env in (
        "ATLAS_RETENTION_DAYS",
        "ATLAS_RETENTION_MEMORY_DAYS",
        *[f"ATLAS_RETENTION_{t.upper()}_DAYS" for t in DEFAULT_DAYS],
    ):
        monkeypatch.delenv(env, raising=False)
    cutoffs = assemble_cutoffs()
    now = datetime.now(timezone.utc)
    assert set(cutoffs) == set(DEFAULT_DAYS) | {MEMORY_TABLE}
    # 长期记忆缺省不过期（D35 安全切片）
    assert cutoffs[MEMORY_TABLE] is None
    for table, days in DEFAULT_DAYS.items():
        cutoff = datetime.fromisoformat(cutoffs[table])
        expected = now - timedelta(days=days)
        assert abs((cutoff - expected).total_seconds()) < 60


def test_memory_retention_is_none_even_when_global_retention_is_set(monkeypatch) -> None:
    """长期记忆不跟随全局 ATLAS_RETENTION_DAYS：运维设的全局回收不能静默删掉长期记忆。"""
    monkeypatch.delenv("ATLAS_RETENTION_MEMORY_DAYS", raising=False)
    monkeypatch.setenv("ATLAS_RETENTION_DAYS", "3")
    assert assemble_cutoffs()[MEMORY_TABLE] is None


def test_memory_retention_is_enabled_only_by_its_own_env(monkeypatch) -> None:
    monkeypatch.setenv("ATLAS_RETENTION_MEMORY_DAYS", "30")
    cutoff_raw = assemble_cutoffs()[MEMORY_TABLE]
    assert cutoff_raw is not None
    cutoff = datetime.fromisoformat(cutoff_raw)
    expected = datetime.now(timezone.utc) - timedelta(days=30)
    assert abs((cutoff - expected).total_seconds()) < 60


@pytest.mark.parametrize("bad", ["not-a-number", "0", "-5"])
def test_memory_retention_invalid_value_fails_safe_to_none(monkeypatch, bad) -> None:
    """非法/非正取值 fail-safe 到不过期（宁保留不误删长期记忆）。"""
    monkeypatch.setenv("ATLAS_RETENTION_MEMORY_DAYS", bad)
    assert assemble_cutoffs()[MEMORY_TABLE] is None


def test_assemble_cutoffs_env_override(monkeypatch) -> None:
    monkeypatch.setenv("ATLAS_RETENTION_AUDIT_DAYS", "7")
    monkeypatch.setenv("ATLAS_RETENTION_DAYS", "5")  # 全局缺省，只影响未单独设置的表
    cutoffs = assemble_cutoffs()
    assert datetime.fromisoformat(cutoffs["audit_events"]) > datetime.now(timezone.utc) - timedelta(days=8)
    assert datetime.fromisoformat(cutoffs["runs"]) > datetime.now(timezone.utc) - timedelta(days=6)


def test_llm_usage_retention_default_and_override(monkeypatch) -> None:
    """docs/129 AU-D7 余部（2026-10-16 闭合）：llm_usage 成本账挂 retention——
    缺省 90 天（对齐 audit/runs），分表 env 可覆盖，也跟随全局 ATLAS_RETENTION_DAYS。"""
    for env in ("ATLAS_RETENTION_DAYS", "ATLAS_RETENTION_LLM_USAGE_DAYS"):
        monkeypatch.delenv(env, raising=False)
    now = datetime.now(timezone.utc)
    cutoff = datetime.fromisoformat(assemble_cutoffs()["llm_usage"])
    assert abs((cutoff - (now - timedelta(days=90))).total_seconds()) < 60
    # 分表 env 覆盖缺省
    monkeypatch.setenv("ATLAS_RETENTION_LLM_USAGE_DAYS", "14")
    cutoff = datetime.fromisoformat(assemble_cutoffs()["llm_usage"])
    assert abs((cutoff - (now - timedelta(days=14))).total_seconds()) < 60
    # 未设分表 env 时跟随全局
    monkeypatch.delenv("ATLAS_RETENTION_LLM_USAGE_DAYS")
    monkeypatch.setenv("ATLAS_RETENTION_DAYS", "30")
    cutoff = datetime.fromisoformat(assemble_cutoffs()["llm_usage"])
    assert abs((cutoff - (now - timedelta(days=30))).total_seconds()) < 60


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
    assert "dry-run: DELETE from llm_usage" in out
    assert "dry-run: retain memory_items (no retention configured)" in out
    assert "dry-run: no rows deleted" in out
