# -*- coding: utf-8 -*-
"""T6 审计日志测试（docs/35 §6，docs/34 P1 #8）。

覆盖：
- AuditStore 进程内 ring：record/list 倒序/limit clamp/action 前缀/export 正序/溢出/环境变量；
- REST（内存档 TestClient）：登录成功显式记一条、登录失败不记、写操作审计且路径参数用路由模板、
  GET 不记、admin only、JSONL 导出附件、format 校验、limit clamp、demo reset 不清审计；
- PgAuditStore 直连集成（ATLAS_RUN_INTEGRATION=1 + DATABASE_URL，不设 ATLAS_STORAGE_BACKEND）。

硬约束断言：审计事件不含请求体/凭据字段（仅 id/tenantId/actor/action/statusCode/path/ip/at）。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from atlas.api.main import app
from atlas.observability.audit import (
    DEFAULT_RING_SIZE,
    AuditStore,
    ring_size_from_env,
)

client = TestClient(app)

_EVENT_KEYS = {"id", "tenantId", "actor", "action", "statusCode", "path", "ip", "at", "seq"}

RUN_INTEGRATION = os.environ.get("ATLAS_RUN_INTEGRATION") == "1"
DATABASE_URL = os.environ.get("DATABASE_URL", "")

pg_integration = pytest.mark.skipif(
    not RUN_INTEGRATION or not DATABASE_URL,
    reason="set ATLAS_RUN_INTEGRATION=1 and DATABASE_URL to run audit PG integration",
)


# ============================ 进程内 AuditStore ============================


def _record(store: AuditStore, n: int, action: str = "POST /api/x") -> None:
    for i in range(n):
        store.record(
            tenant_id="t1",
            actor="admin-a",
            action=action if n == 1 else f"{action}/{i}",
            status_code=200,
            path="/api/x",
            ip="127.0.0.1",
        )


def test_audit_record_list_descending_and_shape():
    store = AuditStore(maxlen=100)
    store.record(tenant_id="t1", actor="admin-a", action="POST /api/feedback",
                 status_code=201, path="/api/feedback", ip="1.2.3.4")
    store.record(tenant_id="t1", actor="op-a", action="DELETE /api/memories/{memory_id}",
                 status_code=200, path="/api/memories/mem-9", ip="1.2.3.4")
    events = store.list()
    assert len(events) == 2
    assert set(events[0].keys()) == _EVENT_KEYS  # 仅元数据，无请求体/凭据
    assert events[0]["id"] == "aud-2"  # 倒序：最新在前
    assert events[0]["action"] == "DELETE /api/memories/{memory_id}"
    assert events[0]["path"] == "/api/memories/mem-9"  # 实际路径与模板分离
    assert events[0]["statusCode"] == 200
    assert events[1]["id"] == "aud-1"


def test_audit_list_limit_clamp():
    store = AuditStore(maxlen=100)
    _record(store, 5)
    assert len(store.list(limit=3)) == 3
    assert len(store.list(limit=0)) == 1  # clamp 到 1
    assert len(store.list(limit=-5)) == 1
    assert len(store.list(limit=10_000)) == 5  # 上限 500，但仅 5 条


def test_audit_action_prefix_filter():
    store = AuditStore(maxlen=100)
    store.record(tenant_id="t1", actor="a", action="POST /api/feedback",
                 status_code=201, path="/api/feedback", ip="")
    store.record(tenant_id="t1", actor="a", action="POST /api/memories",
                 status_code=201, path="/api/memories", ip="")
    store.record(tenant_id="t1", actor="a", action="DELETE /api/memories/{memory_id}",
                 status_code=200, path="/api/memories/mem-1", ip="")
    post_mem = store.list(action_prefix="POST /api/memories")
    assert [e["action"] for e in post_mem] == ["POST /api/memories"]
    del_mem = store.list(action_prefix="DELETE /api/memories")
    assert [e["action"] for e in del_mem] == ["DELETE /api/memories/{memory_id}"]
    assert len(store.list(action_prefix="POST")) == 2  # feedback + memories 两个 POST


def test_audit_export_jsonl_ascending():
    store = AuditStore(maxlen=100)
    _record(store, 3, action="POST /api/x")
    body = store.export_jsonl()
    lines = [ln for ln in body.splitlines() if ln]
    assert len(lines) == 3
    parsed = [json.loads(ln) for ln in lines]
    assert [e["id"] for e in parsed] == ["aud-1", "aud-2", "aud-3"]  # 正序
    assert set(parsed[0].keys()) == _EVENT_KEYS
    # 前缀过滤导出
    assert store.export_jsonl(action_prefix="NONE") == ""


def test_audit_ring_overflow_keeps_newest():
    store = AuditStore(maxlen=2)
    _record(store, 3, action="POST /api/x")
    events = store.list()
    assert len(events) == 2
    assert [e["id"] for e in events] == ["aud-3", "aud-2"]  # 最旧 aud-1 溢出


def test_audit_anonymous_actor_and_empty_ip():
    store = AuditStore(maxlen=10)
    event = store.record(tenant_id="t1", actor="", action="POST /api/auth/login",
                         status_code=200, path="/api/auth/login", ip="")
    assert event["actor"] == "anonymous"
    assert event["ip"] == ""


def test_ring_size_from_env(monkeypatch):
    monkeypatch.delenv("ATLAS_AUDIT_RING", raising=False)
    assert ring_size_from_env() == DEFAULT_RING_SIZE == 2000
    monkeypatch.setenv("ATLAS_AUDIT_RING", "500")
    assert ring_size_from_env() == 500
    for bad in ("0", "-1", "abc", "999999999"):
        monkeypatch.setenv("ATLAS_AUDIT_RING", bad)
        assert ring_size_from_env() == DEFAULT_RING_SIZE


# ============================ REST（内存档） ============================


def _login(role: str) -> dict[str, str]:
    username = {"admin": "admin-a", "operator": "operator-a", "viewer": "viewer-a"}[role]
    password = {"admin": "admin123", "operator": "operator123", "viewer": "viewer123"}[role]
    token = client.post("/api/auth/login", json={"username": username, "password": password}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _clean_audit():
    # 每用例：admin 登录（本身记一条 login），随后清空审计与 demo 数据，得到干净起点。
    headers = _login("admin")
    client.post("/api/demo/reset", headers=headers)
    from atlas.iam.deps import tenant_registry
    tenant_registry.get("t1").audit_store.clear()
    yield headers
    client.headers.pop("authorization", None)


def _events(headers: dict[str, str], **params):
    return client.get("/api/audit/events", headers=headers, params=params)


def test_login_success_is_audited(_clean_audit):
    headers = _login("admin")  # 再登录一次（干净起点之后）
    resp = _events(headers, action="POST /api/auth/login")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["actor"] == "admin-a"
    assert items[0]["action"] == "POST /api/auth/login"
    assert items[0]["statusCode"] == 200


def test_login_failure_not_audited(_clean_audit):
    # 错误密码 → 401，不产生审计（用不存在用户名避免 throttle 干扰）
    bad = client.post("/api/auth/login", json={"username": "nobody-x", "password": "wrong"})
    assert bad.status_code == 401
    headers = _clean_audit
    resp = _events(headers)
    assert resp.json()["items"] == []


def test_write_action_audited_get_not_audited(_clean_audit):
    headers = _clean_audit
    # 一个写操作
    r = client.post("/api/feedback", headers=headers,
                    json={"type": "bug", "content": "审计测试反馈内容"})
    assert r.status_code == 201
    items = _events(headers, action="POST /api/feedback").json()["items"]
    assert len(items) == 1
    assert items[0]["statusCode"] == 201
    assert items[0]["path"] == "/api/feedback"
    # 审计载荷不含请求体内容
    assert "审计测试反馈内容" not in json.dumps(items, ensure_ascii=False)
    # 一个读操作（GET）不应被审计
    assert client.get("/api/feedback", headers=headers).status_code == 200
    get_events = [e for e in _events(headers).json()["items"] if e["action"].startswith("GET")]
    assert get_events == []


def test_path_parameter_action_uses_route_template(_clean_audit):
    headers = _clean_audit
    created = client.post("/api/memories", headers=headers,
                          json={"kind": "fact", "content": "审计模板化测试记忆"}).json()
    mid = created["id"]
    assert client.delete(f"/api/memories/{mid}", headers=headers).status_code == 200
    events = _events(headers, action="DELETE /api/memories").json()["items"]
    assert len(events) == 1
    assert events[0]["action"] == "DELETE /api/memories/{memory_id}"  # 模板，不含真实 id
    assert events[0]["path"] == f"/api/memories/{mid}"  # 实际 path 单独存


def test_audit_events_requires_admin(_clean_audit):
    assert _events({}).status_code == 401  # 未认证
    assert _events(_login("viewer")).status_code == 403
    assert _events(_login("operator")).status_code == 403
    assert _events(_login("admin")).status_code == 200


def test_audit_events_limit_clamp(_clean_audit):
    headers = _clean_audit
    for _ in range(3):
        client.post("/api/feedback", headers=headers, json={"type": "bug", "content": "x"})
    resp = _events(headers, limit=999_999)
    assert resp.status_code == 200
    assert resp.json()["limit"] == 500
    assert len(resp.json()["items"]) == 3
    assert len(_events(headers, limit=1).json()["items"]) == 1


def test_export_jsonl_attachment(_clean_audit):
    headers = _clean_audit
    client.post("/api/feedback", headers=headers, json={"type": "bug", "content": "导出a"})
    client.post("/api/feedback", headers=headers, json={"type": "suggestion", "content": "导出b"})
    resp = client.get("/api/audit/export", headers=headers, params={"format": "jsonl"})
    assert resp.status_code == 200
    assert "attachment" in resp.headers["content-disposition"]
    assert "atlas-audit.jsonl" in resp.headers["content-disposition"]
    assert "ndjson" in resp.headers["content-type"]
    lines = [ln for ln in resp.text.splitlines() if ln]
    assert len(lines) == 2
    parsed = [json.loads(ln) for ln in lines]
    assert [e["action"] for e in parsed] == ["POST /api/feedback", "POST /api/feedback"]  # 正序


def test_export_unsupported_format_422(_clean_audit):
    resp = client.get("/api/audit/export", headers=_clean_audit, params={"format": "csv"})
    assert resp.status_code == 422


def test_demo_reset_does_not_clear_audit(_clean_audit):
    headers = _clean_audit
    client.post("/api/feedback", headers=headers, json={"type": "bug", "content": "reset 前保留"})
    assert client.post("/api/demo/reset", headers=headers).status_code == 200
    actions = [e["action"] for e in _events(headers).json()["items"]]
    assert "POST /api/feedback" in actions  # reset 前的审计仍在
    assert "POST /api/demo/reset" in actions  # reset 自身也被审计


# ============================ PG 直连集成 ============================


def _run_all_migrations(engine) -> None:
    from sqlalchemy import text

    migrations_dir = Path(__file__).resolve().parents[1] / "db" / "migrations"
    for path in sorted(migrations_dir.glob("*.sql")):
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


@pytest.mark.integration
@pg_integration
def test_pg_audit_store_roundtrip_list_export():
    from atlas.memory.database import create_database_engine
    from atlas.storage.pg import PgBackend

    tenant = "pgaudittest"
    engine = create_database_engine(DATABASE_URL, pool_size=2)
    _run_all_migrations(engine)
    with engine.begin() as conn:
        from sqlalchemy import text
        conn.execute(text("DELETE FROM audit_events WHERE tenant_id = :t"), {"t": tenant})
    try:
        store = PgBackend(engine).audit_store(tenant)
        e1 = store.record(tenant_id=tenant, actor="admin-a", action="POST /api/feedback",
                          status_code=201, path="/api/feedback", ip="9.9.9.9")
        e2 = store.record(tenant_id=tenant, actor="admin-a",
                          action="DELETE /api/memories/{memory_id}",
                          status_code=200, path="/api/memories/mem-1", ip="9.9.9.9")
        assert e1["id"].startswith("aud-") and e2["id"].startswith("aud-")
        assert set(e1.keys()) == _EVENT_KEYS

        desc = store.list()
        assert [e["action"] for e in desc] == [
            "DELETE /api/memories/{memory_id}", "POST /api/feedback"
        ]
        filtered = store.list(action_prefix="DELETE /api/memories")
        assert len(filtered) == 1 and filtered[0]["path"] == "/api/memories/mem-1"

        body = store.export_jsonl()
        parsed = [json.loads(ln) for ln in body.splitlines() if ln]
        assert [e["action"] for e in parsed] == [
            "POST /api/feedback", "DELETE /api/memories/{memory_id}"
        ]  # 正序

        assert len(store.list(limit=1)) == 1
        store.clear()
        assert store.list() == []
    finally:
        with engine.begin() as conn:
            from sqlalchemy import text
            conn.execute(text("DELETE FROM audit_events WHERE tenant_id = :t"), {"t": tenant})
        engine.dispose()
