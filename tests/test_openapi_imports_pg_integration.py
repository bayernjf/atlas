# -*- coding: utf-8 -*-
"""导入规格 PG 集成测试（docs/43 §5；需 ATLAS_RUN_INTEGRATION=1 + DATABASE_URL）。

前置：002_storage.sql（storage_id_seq）与 018_openapi_imports.sql 已可应用。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import text

DATABASE_URL = os.environ.get("DATABASE_URL", "")
RUN_INTEGRATION = os.environ.get("ATLAS_RUN_INTEGRATION", "") == "1"

pytestmark = pytest.mark.skipif(
    not RUN_INTEGRATION or not DATABASE_URL,
    reason="set ATLAS_RUN_INTEGRATION=1 and DATABASE_URL to run openapi PG integration",
)


def _run_migration(engine, name: str) -> None:
    path = Path(__file__).resolve().parents[1] / "db" / "migrations" / name
    statements: list[str] = []
    current: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        current.append(line)
        if stripped.endswith(";"):
            statements.append("\n".join(current))
            current = []
    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))


@pytest.fixture(scope="module")
def setup():
    from atlas.memory.database import create_database_engine
    from atlas.openapi.pg_store import PgImportStore

    engine = create_database_engine(DATABASE_URL, pool_size=2)
    _run_migration(engine, "002_storage.sql")
    _run_migration(engine, "018_openapi_imports.sql")
    _run_migration(engine, "019_openapi_auth_columns.sql")
    yield engine, PgImportStore
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM openapi_imports WHERE tenant_id LIKE 'oipit%'"))
    engine.dispose()


def _spec(title="Petstore Pit", base_url="https://petstore.example.com", names=("list_pets", "create_pet")):
    from atlas.openapi.models import OperationDescriptor, ParsedSpec

    return ParsedSpec(
        title=title,
        version="3.0.3",
        base_url=base_url,
        operations=[
            OperationDescriptor(
                name=n,
                method="get" if n.startswith("list") else "post",
                path="/v1/pets",
                summary="pets",
                permission="read" if n.startswith("list") else "write",
                idempotent=n.startswith("list"),
                input_schema={"type": "object", "properties": {"limit": {"type": "integer"}}},
                locations={"limit": "query"},
            )
            for n in names
        ],
    )


def test_add_roundtrip(setup):
    engine, Store = setup
    store = Store(engine, "oipit-a")
    imported = store.add(_spec())
    assert imported.spec_id.startswith("openapi-")
    assert imported.title == "Petstore Pit"
    assert imported.base_url == "https://petstore.example.com"
    assert imported.created_at
    assert [op.name for op in imported.operations] == ["list_pets", "create_pet"]

    loaded = store.get(imported.spec_id)
    assert loaded is not None
    assert loaded.model_dump() == imported.model_dump()
    first = loaded.operations[0]
    assert first.permission == "read" and first.idempotent is True
    assert first.input_schema["properties"]["limit"]["type"] == "integer"
    assert first.locations == {"limit": "query"}


def test_skipped_operations_are_dropped(setup):
    engine, Store = setup
    from atlas.openapi.models import OperationDescriptor, ParsedSpec

    store = Store(engine, "oipit-a")
    spec = ParsedSpec(
        title="Skip Pit",
        base_url="https://skip.example.com",
        operations=[
            OperationDescriptor(name="ok_op", method="get", path="/ok"),
            OperationDescriptor(
                name="bad_op", method="get", path="/bad",
                skipped=True, skip_reason="不支持的关键字 oneOf",
            ),
        ],
    )
    imported = store.add(spec)
    assert [op.name for op in imported.operations] == ["ok_op"]


def test_seq_ids_ordering_and_restart(setup):
    engine, Store = setup
    store = Store(engine, "oipit-order")
    first = store.add(_spec(title="First"))
    second = store.add(_spec(title="Second"))
    assert int(second.spec_id.split("-")[1]) > int(first.spec_id.split("-")[1])

    titles = [s.title for s in store.list()]
    assert titles.index("First") < titles.index("Second")

    # 模拟重启：新 store 实例读同库，数据仍在且顺序不变
    reopened = Store(engine, "oipit-order")
    assert [s.spec_id for s in reopened.list()] == [first.spec_id, second.spec_id]


def test_cross_tenant_invisible(setup):
    engine, Store = setup
    store_a = Store(engine, "oipit-a")
    store_b = Store(engine, "oipit-b")
    imported = store_b.add(_spec(title="Tenant B Only"))
    assert store_a.get(imported.spec_id) is None
    assert all(s.title != "Tenant B Only" for s in store_a.list())


def test_delete_idempotent(setup):
    engine, Store = setup
    store = Store(engine, "oipit-del")
    imported = store.add(_spec())
    assert store.delete(imported.spec_id) is True
    assert store.get(imported.spec_id) is None
    assert store.delete(imported.spec_id) is False


def test_spec_limit_enforced(setup):
    engine, Store = setup
    from atlas.openapi.store import ImportStoreError

    store = Store(engine, "oipit-limit")
    for i in range(5):
        store.add(_spec(title=f"Spec {i}"))
    with pytest.raises(ImportStoreError) as exc:
        store.add(_spec(title="Overflow"))
    assert exc.value.code == "OPENAPI_LIMIT_EXCEEDED"
    assert exc.value.status_code == 422
    assert len(store.list()) == 5


def test_operations_limit_enforced(setup):
    engine, Store = setup
    from atlas.openapi.models import OperationDescriptor, ParsedSpec
    from atlas.openapi.store import ImportStoreError, MAX_OPERATIONS_PER_SPEC

    store = Store(engine, "oipit-ops")
    names = [f"op_{i}" for i in range(MAX_OPERATIONS_PER_SPEC + 1)]
    spec = ParsedSpec(
        title="Too Many Ops",
        base_url="https://ops.example.com",
        operations=[OperationDescriptor(name=n, method="get", path=f"/{n}") for n in names],
    )
    with pytest.raises(ImportStoreError) as exc:
        store.add(spec)
    assert exc.value.code == "OPENAPI_LIMIT_EXCEEDED"
    assert store.list() == []


def test_imports_survive_reset(setup):
    from sqlalchemy.exc import OperationalError

    engine, Store = setup
    from atlas.iam.registry import TenantRegistry

    store = Store(engine, "oipit-reset")
    imported = store.add(_spec(title="Reset Pit"))

    try:
        TenantRegistry().reset_tenant("oipit-reset")
    except OperationalError:
        pytest.skip("tenant reset requires full schema in integration database")
    assert store.get(imported.spec_id) is not None


# --- security schemes + credential envelopes（docs/44） --------------------

def _secured_spec():
    from atlas.openapi.models import OperationDescriptor, ParsedSpec, SecurityScheme

    return ParsedSpec(
        title="Secured Pit",
        base_url="https://secured.example.com",
        operations=[
            OperationDescriptor(
                name="secured_op",
                method="get",
                path="/things",
                security=[["KeyHeader"], ["BearerAuth"]],
            )
        ],
        security_schemes={
            "KeyHeader": SecurityScheme(
                name="KeyHeader", kind="api_key", location="header", param="X-API-Key"
            ),
            "BearerAuth": SecurityScheme(
                name="BearerAuth",
                kind="bearer",
                location=None,
                param="Authorization",
                prefix="Bearer ",
            ),
        },
    )


def test_auth_fields_roundtrip(setup):
    engine, Store = setup
    from atlas.security.secrets import PlaintextSecretProvider

    provider = PlaintextSecretProvider()
    store = Store(engine, "oipit-auth")
    envelopes = {"KeyHeader": provider.encrypt("secret-key")}
    imported = store.add(_secured_spec(), envelopes=envelopes)
    assert set(imported.security_schemes) == {"KeyHeader", "BearerAuth"}
    assert set(imported.credential_envelopes) == {"KeyHeader"}

    loaded = store.get(imported.spec_id)
    assert loaded is not None
    assert loaded.model_dump() == imported.model_dump()
    assert provider.decrypt(loaded.credential_envelopes["KeyHeader"]) == "secret-key"


def test_envelopes_survive_restart_and_put(setup):
    engine, Store = setup
    from atlas.security.secrets import PlaintextSecretProvider

    provider = PlaintextSecretProvider()
    tenant = "oipit-restart"
    store = Store(engine, tenant)
    imported = store.add(
        _secured_spec(),
        envelopes={"KeyHeader": provider.encrypt("k1")},
    )

    reopened = Store(engine, tenant)
    reloaded = reopened.get(imported.spec_id)
    assert reloaded is not None
    assert provider.decrypt(reloaded.credential_envelopes["KeyHeader"]) == "k1"

    updated = reopened.put_credentials(
        imported.spec_id,
        {
            "KeyHeader": provider.encrypt("k2"),
            "BearerAuth": provider.encrypt("tok"),
        },
    )
    assert updated is not None
    assert set(updated.credential_envelopes) == {"KeyHeader", "BearerAuth"}
    assert Store(engine, tenant).get(imported.spec_id).credential_envelopes == updated.credential_envelopes


def test_legacy_rows_default_to_empty_auth_fields(setup):
    engine, Store = setup
    store = Store(engine, "oipit-legacy")
    imported = store.add(_spec(title="Legacy Pit"))
    assert imported.security_schemes == {}
    assert imported.credential_envelopes == {}
    assert store.put_credentials(imported.spec_id, {}) is not None
