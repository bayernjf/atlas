"""数据适配器（通用 SQL）单元测试（13 文档 U26/I12-I13）。

全部经注入的 SQLite 单连接内存库（StaticPool）执行，零外部依赖。
"""

from __future__ import annotations

import pytest

from atlas.database.adapter import DatabaseHarnessAdapter
from atlas.database.service import (
    DEFAULT_LIMIT,
    DatabaseAdapterError,
    DatabaseClient,
    demo_engine,
)
from atlas.harness.base import ActionRequest, ActionStatus, Permission


def make_client(seed: bool = True) -> DatabaseClient:
    return DatabaseClient(demo_engine(seed=seed), demo=True)


def make_adapter(client=None, granted=None) -> DatabaseHarnessAdapter:
    return DatabaseHarnessAdapter(
        client=client or make_client(),
        granted_permissions=granted
        or {Permission.READ, Permission.WRITE, Permission.DELETE, Permission.FINANCIAL},
    )


# --- service: query ---

def test_query_returns_seeded_rows():
    output = make_client().query("SELECT order_id, reason, amount FROM orders ORDER BY order_id")

    assert output["columns"] == ["order_id", "reason", "amount"]
    assert output["row_count"] == 2
    assert output["truncated"] is False
    assert output["rows"][0] == {"order_id": "12345", "reason": "商品破损", "amount": 299}


def test_query_named_bound_params_filter():
    output = make_client().query(
        "SELECT order_id FROM orders WHERE amount > :min ORDER BY order_id",
        {"min": 1000},
    )

    assert [row["order_id"] for row in output["rows"]] == ["12346"]


def test_query_limit_truncates_and_flags():
    output = make_client().query("SELECT order_id FROM orders ORDER BY order_id", {}, limit=1)

    assert output["row_count"] == 1
    assert output["truncated"] is True


def test_query_default_limit_is_500():
    client = make_client()

    assert client.query("SELECT 1", {}, limit=DEFAULT_LIMIT)["rows"] == [{"1": 1}]


def test_query_does_not_mutate_data():
    client = make_client()
    client.query("SELECT * FROM orders")

    assert client.query("SELECT COUNT(*) AS n FROM orders")["rows"][0]["n"] == 2


def test_query_bad_sql_is_db_sql_error():
    with pytest.raises(DatabaseAdapterError) as exc_info:
        make_client().query("SELECT FROM nope")

    assert exc_info.value.code == "DB_SQL_ERROR"


def test_query_missing_sql():
    with pytest.raises(DatabaseAdapterError) as exc_info:
        make_client().query("   ")

    assert exc_info.value.code == "MISSING_PARAMETER"


@pytest.mark.parametrize("bad_params", ["x", 1, [1, 2], [{"ok": 1}, "x"]])
def test_query_invalid_params(bad_params):
    with pytest.raises(DatabaseAdapterError) as exc_info:
        make_client().query("SELECT 1", bad_params)

    assert exc_info.value.code == "INVALID_PARAMETER"


@pytest.mark.parametrize("bad_limit", [0, 1001, "5", 1.5, True, None])
def test_query_invalid_limit(bad_limit):
    with pytest.raises(DatabaseAdapterError) as exc_info:
        make_client().query("SELECT 1", {}, limit=bad_limit)

    assert exc_info.value.code == "INVALID_PARAMETER"


# --- service: execute ---

def test_execute_insert_rowcount_visible_to_query():
    client = make_client()

    output = client.execute(
        "INSERT INTO orders (order_id, reason, amount, status) "
        "VALUES (:order_id, :reason, :amount, :status)",
        {"order_id": "12347", "reason": "重复扣款", "amount": 88, "status": "pending_refund"},
    )

    assert output == {"rowcount": 1}
    rows = client.query("SELECT order_id FROM orders WHERE order_id = '12347'")["rows"]
    assert rows == [{"order_id": "12347"}]


def test_execute_bad_sql_is_db_sql_error():
    with pytest.raises(DatabaseAdapterError) as exc_info:
        make_client().execute("INSERT INTO nope VALUES (1)")

    assert exc_info.value.code == "DB_SQL_ERROR"


def test_execute_update_rowcount():
    output = make_client().execute(
        "UPDATE orders SET status = :status WHERE amount > :min",
        {"status": "reviewed", "min": 1000},
    )

    assert output == {"rowcount": 1}


def test_reseed_restores_seed_rows():
    client = make_client()
    client.execute("DELETE FROM orders")
    assert client.query("SELECT COUNT(*) AS n FROM orders")["rows"][0]["n"] == 0

    client.reseed_demo()

    assert client.query("SELECT COUNT(*) AS n FROM orders")["rows"][0]["n"] == 2


# --- engine / env ---

def test_query_missing_table_is_db_sql_error():
    fresh = DatabaseClient(demo_engine(seed=False), demo=True)
    with pytest.raises(DatabaseAdapterError) as exc_info:
        fresh.query("SELECT * FROM orders")

    assert exc_info.value.code == "DB_SQL_ERROR"


def test_from_env_none_when_unset(monkeypatch):
    monkeypatch.delenv("ATLAS_DATABASE_URL", raising=False)

    assert DatabaseClient.from_env() is None


def test_from_env_sqlite_file_is_read_only(monkeypatch, tmp_path):
    # docs/32 §4：外部数据源连接（含 sqlite 文件档）query 只读、execute fail-closed
    from sqlalchemy import create_engine, text

    db_path = tmp_path / "demo.db"
    seeder = create_engine(f"sqlite:///{db_path}")
    with seeder.begin() as conn:  # 独立可写连接模拟外部库已有数据
        conn.execute(text("CREATE TABLE t (id INTEGER PRIMARY KEY)"))
        conn.execute(text("INSERT INTO t (id) VALUES (1)"))
    seeder.dispose()
    assert db_path.exists()

    monkeypatch.setenv("ATLAS_DATABASE_URL", f"sqlite:///{db_path}")
    client = DatabaseClient.from_env()

    assert client is not None
    assert client.demo is False
    assert client.read_only is True
    assert client.query("SELECT id FROM t ORDER BY id")["rows"] == [{"id": 1}]
    with pytest.raises(DatabaseAdapterError) as exc_info:
        client.execute("INSERT INTO t (id) VALUES (2)")
    assert exc_info.value.code == "DB_WRITE_FORBIDDEN"
    client.engine.dispose()


def test_from_env_rejects_unknown_scheme(monkeypatch):
    monkeypatch.setenv("ATLAS_DATABASE_URL", "mysql://user:pw@localhost/db")

    with pytest.raises(ValueError):
        DatabaseClient.from_env()


def test_masked_url_hides_password(monkeypatch):
    monkeypatch.setenv(
        "ATLAS_DATABASE_URL",
        "postgresql+psycopg://atlas:supersecret@db.internal:5432/orders",
    )

    client = DatabaseClient.from_env()

    try:
        masked = client.masked_url
        assert "supersecret" not in masked
        assert "***" in masked
        assert "db.internal" in masked
    finally:
        client.engine.dispose()


# --- adapter ---

def test_adapter_declares_two_capabilities():
    capabilities = make_adapter().list_capabilities()

    assert [(c.name, c.permission, c.is_idempotent) for c in capabilities] == [
        ("query", Permission.READ, True),
        ("execute", Permission.WRITE, False),
    ]


def test_adapter_query_success():
    result = make_adapter().execute(
        ActionRequest(capability_name="query", parameters={"sql": "SELECT order_id FROM orders ORDER BY order_id"})
    )

    assert result.status is ActionStatus.SUCCESS
    assert result.output["row_count"] == 2


def test_adapter_execute_success():
    result = make_adapter().execute(
        ActionRequest(
            capability_name="execute",
            parameters={
                "sql": "INSERT INTO orders (order_id, reason, amount, status) VALUES ('1', 'x', 1, 's')"
            },
        )
    )

    assert result.status is ActionStatus.SUCCESS
    assert result.output["rowcount"] == 1


def test_adapter_maps_service_error():
    result = make_adapter().execute(ActionRequest(capability_name="query", parameters={}))

    assert result.status is ActionStatus.FAILED
    assert result.error.code == "MISSING_PARAMETER"


def test_adapter_execute_requires_write_permission_with_audit():
    audited = []
    adapter = make_adapter(
        granted={Permission.READ},
    )
    adapter.audit_sink = lambda adapter_id, name, req: audited.append((adapter_id, name))

    result = adapter.execute(
        ActionRequest(capability_name="execute", parameters={"sql": "DELETE FROM orders"})
    )

    assert result.status is ActionStatus.FAILED
    assert result.error.code == "PERMISSION_DENIED"
    assert audited == [("database", "execute")]


def test_adapter_query_requires_read_permission_with_audit():
    audited = []
    adapter = DatabaseHarnessAdapter(
        client=make_client(),
        granted_permissions={Permission.WRITE},
        audit_sink=lambda adapter_id, name, req: audited.append((adapter_id, name)),
    )

    result = adapter.execute(ActionRequest(capability_name="query", parameters={"sql": "SELECT 1"}))

    assert result.status is ActionStatus.FAILED
    assert result.error.code == "PERMISSION_DENIED"
    assert audited == [("database", "query")]


def test_adapter_unknown_capability():
    result = make_adapter().execute(ActionRequest(capability_name="nope", parameters={}))

    assert result.status is ActionStatus.FAILED
    assert result.error.code == "UNKNOWN_CAPABILITY"


def test_adapter_unconfigured_is_fail_closed(monkeypatch):
    monkeypatch.delenv("ATLAS_DATABASE_URL", raising=False)
    adapter = DatabaseHarnessAdapter(
        granted_permissions={Permission.READ, Permission.WRITE},
    )

    result = adapter.execute(ActionRequest(capability_name="query", parameters={"sql": "SELECT 1"}))

    assert result.status is ActionStatus.FAILED
    assert result.error.code == "DB_NOT_CONFIGURED"
    assert adapter.observe().url == "obs://database?unconfigured"


def test_adapter_observe_reports_masked_url_and_last_operation():
    adapter = make_adapter()
    adapter.execute(ActionRequest(capability_name="query", parameters={"sql": "SELECT 1 AS one"}))

    observation = adapter.observe()

    assert observation.url.startswith("obs://database?")
    assert observation.data["demo"] is True
    assert observation.data["last_operation"]["operation"] == "query"
    assert observation.data["last_operation"]["row_count"] == 1


# --- docs/32 §4 只读强制（13 文档 U239 db 通道）---

def make_external_readonly_client() -> DatabaseClient:
    """以外部数据源身份（demo=False → read_only=True）连内存库承载数据。"""
    return DatabaseClient(demo_engine(seed=True), url="sqlite:///:memory:", demo=False)


def test_client_read_only_default_by_demo_flag():
    assert make_external_readonly_client().read_only is True
    assert make_client().read_only is False


def test_external_readonly_query_select_ok():
    output = make_external_readonly_client().query("SELECT COUNT(*) AS n FROM orders")
    assert output["rows"][0]["n"] == 2


def test_external_readonly_execute_forbidden_before_touching_db():
    client = make_external_readonly_client()
    with pytest.raises(DatabaseAdapterError) as exc_info:
        client.execute(
            "INSERT INTO orders (order_id) VALUES (:x)",
            {"x": "z"},
        )
    assert exc_info.value.code == "DB_WRITE_FORBIDDEN"
    # fail-closed 发生在触库前：行数不变
    assert client.query("SELECT COUNT(*) AS n FROM orders")["rows"][0]["n"] == 2


def test_query_channel_rejects_write_sql_even_on_writable_demo_client():
    # 两档都强制 query 只读：即便 demo client 可写，query 通道写 SQL 仍被静态审查拒
    with pytest.raises(DatabaseAdapterError) as exc_info:
        make_client().query("DELETE FROM orders")
    assert exc_info.value.code == "DB_SQL_NOT_READ_ONLY"


def test_external_readonly_query_write_sql_rejected():
    with pytest.raises(DatabaseAdapterError) as exc_info:
        make_external_readonly_client().query("UPDATE orders SET status = 'x'")
    assert exc_info.value.code == "DB_SQL_NOT_READ_ONLY"


def test_explicit_read_only_override_blocks_demo_execute():
    client = DatabaseClient(demo_engine(seed=True), demo=True, read_only=True)
    assert client.read_only is True
    with pytest.raises(DatabaseAdapterError) as exc_info:
        client.execute("INSERT INTO orders (order_id) VALUES ('x')")
    assert exc_info.value.code == "DB_WRITE_FORBIDDEN"


def test_adapter_execute_on_readonly_client_folds_write_forbidden():
    adapter = make_adapter(client=make_external_readonly_client())
    result = adapter.execute(
        ActionRequest(
            capability_name="execute",
            parameters={"sql": "INSERT INTO orders (order_id) VALUES ('1')"},
        )
    )
    assert result.status is ActionStatus.FAILED
    assert result.error.code == "DB_WRITE_FORBIDDEN"


def test_adapter_query_write_sql_folds_not_read_only():
    result = make_adapter().execute(
        ActionRequest(capability_name="query", parameters={"sql": "DROP TABLE orders"})
    )
    assert result.status is ActionStatus.FAILED
    assert result.error.code == "DB_SQL_NOT_READ_ONLY"
