# -*- coding: utf-8 -*-
"""迁移运行器纯逻辑测试（docs/30 §7，U222；常跑、无 DB）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from atlas.storage.migrations import (
    apply_pending,
    default_migrations_dir,
    ensure_schema_migrations,
    list_migration_versions,
)


class FakeCursor:
    def __init__(self, executed: list[str], fail_on: str | None) -> None:
        self._executed = executed
        self._fail_on = fail_on

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql: str) -> None:
        if self._fail_on and self._fail_on in sql:
            raise RuntimeError("boom")
        self._executed.append(sql)


class FakeDBAPI:
    def __init__(self, executed: list[str], fail_on: str | None = None) -> None:
        self._executed = executed
        self._fail_on = fail_on

    @property
    def cursor(self):
        return lambda: FakeCursor(self._executed, self._fail_on)


class _CM:
    def __init__(self, value):
        self._value = value

    def __enter__(self):
        return self._value

    def __exit__(self, *exc):
        return False


class FakeConn:
    def __init__(self, inserts: list[dict], executed_sql: list[str], fail_on: str | None) -> None:
        self.inserts = inserts
        self.executed_sql = executed_sql
        self.connection = type("C", (), {"dbapi_connection": FakeDBAPI(executed_sql, fail_on)})()

    def execute(self, statement, params=None):
        text_value = str(statement)
        if "INSERT INTO schema_migrations" in text_value:
            self.inserts.append(dict(params or {}))
        else:
            self.executed_sql.append(text_value)


class FakeEngine:
    def __init__(
        self,
        applied: set[str],
        *,
        fail_on: str | None = None,
    ) -> None:
        self.applied = applied
        self.ensured = False
        self.inserts: list[dict] = []
        self.executed_sql: list[str] = []
        self._fail_on = fail_on

    def begin(self):
        return _CM(FakeConn(self.inserts, self.executed_sql, self._fail_on))

    def connect(self):
        rows = [(v,) for v in sorted(self.applied)]

        class Result:
            def __init__(self, rows):
                self._rows = rows

            def __iter__(self):
                return iter(self._rows)

        conn = type("Conn", (), {})()
        conn.execute = lambda statement: Result(rows)
        return _CM(conn)


@pytest.fixture()
def migrations_dir(tmp_path: Path) -> Path:
    (tmp_path / "001_a.sql").write_text("CREATE TABLE a (id INT);", encoding="utf-8")
    (tmp_path / "002_b.sql").write_text("CREATE TABLE b (id INT);", encoding="utf-8")
    (tmp_path / "010_c.sql").write_text("CREATE TABLE c (id INT);", encoding="utf-8")
    return tmp_path


def test_list_versions_sorted(migrations_dir: Path) -> None:
    assert list_migration_versions(migrations_dir) == ["001_a.sql", "002_b.sql", "010_c.sql"]


def test_default_migrations_dir_env_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "custom_migrations"
    target.mkdir()
    monkeypatch.setenv("ATLAS_MIGRATIONS_DIR", str(target))
    assert default_migrations_dir() == target


def test_ensure_schema_migrations_runs_ddl() -> None:
    engine = FakeEngine(set())
    ensure_schema_migrations(engine)
    assert any("schema_migrations" in sql for sql in engine.executed_sql)


def _business_sql(engine: FakeEngine) -> list[str]:
    return [
        s.strip()
        for s in engine.executed_sql
        if "CREATE TABLE IF NOT EXISTS schema_migrations" not in s
    ]


def test_apply_pending_executes_and_records_in_order(migrations_dir: Path) -> None:
    engine = FakeEngine(set())
    processed = apply_pending(engine, migrations_dir)
    assert processed == ["001_a.sql", "002_b.sql", "010_c.sql"]
    assert [v["version"] for v in engine.inserts] == processed
    assert _business_sql(engine) == [
        "CREATE TABLE a (id INT);",
        "CREATE TABLE b (id INT);",
        "CREATE TABLE c (id INT);",
    ]


def test_apply_pending_skips_applied(migrations_dir: Path) -> None:
    engine = FakeEngine({"001_a.sql", "002_b.sql"})
    processed = apply_pending(engine, migrations_dir)
    assert processed == ["010_c.sql"]
    assert _business_sql(engine) == ["CREATE TABLE c (id INT);"]


def test_failure_propagates_and_not_recorded(migrations_dir: Path) -> None:
    engine = FakeEngine(set(), fail_on="CREATE TABLE b")
    with pytest.raises(RuntimeError, match="boom"):
        apply_pending(engine, migrations_dir)
    assert all(v["version"] != "002_b.sql" for v in engine.inserts)
    assert [v["version"] for v in engine.inserts] == ["001_a.sql"]


def test_mark_existing_registers_without_executing(migrations_dir: Path) -> None:
    engine = FakeEngine(set())
    processed = apply_pending(engine, migrations_dir, mark_existing=True)
    assert processed == ["001_a.sql", "002_b.sql", "010_c.sql"]
    assert _business_sql(engine) == []
    assert [v["version"] for v in engine.inserts] == processed


def test_second_run_is_noop(migrations_dir: Path) -> None:
    engine = FakeEngine(set())
    first = apply_pending(engine, migrations_dir)
    engine.applied = set(first)
    engine.executed_sql.clear()
    engine.inserts.clear()
    second = apply_pending(engine, migrations_dir)
    assert second == []
    assert _business_sql(engine) == []
    assert engine.inserts == []
