# -*- coding: utf-8 -*-
"""U31：种子认证、SessionStore、角色矩阵与 TenantRegistry 分区/重置纯逻辑（04 §5.14）。"""

import re


from atlas.api.main import FeedbackRequest
from atlas.iam.principals import (
    Role,
    authenticate,
    can,
)
from atlas.iam.deps import tenant_registry
from atlas.iam.sessions import SessionStore

TOKEN_RE = re.compile(r"^sess-[0-9a-f]{32}$")

# 注册表是 main 共享单例；用未被种子占用的租户 id 隔离本用例，避免与 API 用例串数据。
_TID = "test-u31"


def test_authenticate_seed_accounts():
    admin = authenticate("admin-a", "admin123")
    assert admin is not None
    assert admin.tenant_id == "t1"
    assert admin.tenant_name == "演示企业 A"
    assert admin.username == "admin-a"
    assert admin.role == Role.ADMIN

    operator = authenticate("operator-a", "operator123")
    assert operator is not None and operator.role == Role.OPERATOR

    viewer = authenticate("viewer-a", "viewer123")
    assert viewer is not None and viewer.role == Role.VIEWER

    admin_b = authenticate("admin-b", "admin123")
    assert admin_b is not None
    assert admin_b.tenant_id == "t2"
    assert admin_b.tenant_name == "演示企业 B"


def test_authenticate_bad_credentials():
    assert authenticate("admin-a", "wrong-password") is None
    assert authenticate("nobody", "admin123") is None
    assert authenticate("ADMIN-A", "admin123") is None
    assert authenticate("", "") is None


def test_role_capability_matrix():
    assert can(Role.VIEWER, "read")
    assert not can(Role.VIEWER, "operate")
    assert not can(Role.VIEWER, "administer")

    assert can(Role.OPERATOR, "read")
    assert can(Role.OPERATOR, "operate")
    assert not can(Role.OPERATOR, "administer")

    assert can(Role.ADMIN, "read")
    assert can(Role.ADMIN, "operate")
    assert can(Role.ADMIN, "administer")


def test_session_store_lifecycle():
    store = SessionStore()
    principal = authenticate("viewer-a", "viewer123")
    assert principal is not None

    token = store.issue(principal)
    assert TOKEN_RE.match(token)

    again = store.issue(principal)
    assert again != token

    resolved = store.principal_for_token(token)
    assert resolved is not None
    assert resolved.username == "viewer-a"
    assert resolved.tenant_id == "t1"

    assert store.principal_for_token(None) is None
    assert store.principal_for_token("sess-deadbeef") is None
    assert store.principal_for_token("not-a-token") is None

    store.revoke(token)
    assert store.principal_for_token(token) is None
    store.revoke(token)  # 幂等

    assert store.principal_for_token(again) is not None
    store.reset()
    assert store.principal_for_token(again) is None


def test_session_returns_principal_copy():
    store = SessionStore()
    principal = authenticate("admin-a", "admin123")
    assert principal is not None
    token = store.issue(principal)
    resolved = store.principal_for_token(token)
    assert resolved is not None
    assert resolved is not principal


def test_tenant_services_are_per_tenant_instances():
    a = tenant_registry.get("t1")
    assert tenant_registry.get("t1") is a

    b = tenant_registry.get("t2")
    assert b is not a
    for field in (
        "graph_store",
        "recording_store",
        "feedback_store",
        "message_service",
        "approval_broker",
        "debug_broker",
        "monitoring",
    ):
        assert getattr(a, field) is not getattr(b, field)


def test_graph_ids_partition_per_tenant():
    services = tenant_registry.get(_TID)
    other = tenant_registry.get(_TID + "-other")

    services.graph_store.save({"nodes": []})
    assert services.graph_store.list()[0]["id"] == "graph-1"
    assert other.graph_store.list() == []

    other.graph_store.save({"nodes": [{"id": "n1"}]})
    assert [item["id"] for item in other.graph_store.list()] == ["graph-1"]
    assert services.graph_store.get("graph-1") is not None
    assert other.graph_store.get("graph-1") is not None
    assert other.graph_store.get("graph-2") is None


def test_reset_tenant_scopes_and_retentions():
    services = tenant_registry.get(_TID + "-reset")
    neighbor = tenant_registry.get(_TID + "-neighbor")

    services.graph_store.save({"nodes": []})
    services.message_service.send("email", "a@example.com", "subject", "body")
    services.recording_store.add(
        name="保留用例",
        graph={"version": 1, "nodes": [], "edges": []},
        inputs=None,
        steps=[{"node_id": "trigger-1", "node_type": "trigger", "output": {}}],
        status="completed",
    )
    services.feedback_store.add(
        FeedbackRequest(type="bug", content="保留反馈", contact="")
    )
    raw_rules = services.monitoring.get_rules().model_dump()
    raw_rules["consecutive_failures"]["threshold"] = 2
    services.monitoring.update_rules(raw_rules)
    assert services.monitoring.get_rules().consecutive_failures.threshold == 2

    neighbor.graph_store.save({"nodes": []})

    tenant_registry.reset_tenant(_TID + "-reset")

    assert services.graph_store.list() == []
    assert services.message_service.list() == []
    assert services.monitoring.get_rules().consecutive_failures.threshold == 3
    assert len(services.recording_store.list()) == 1
    assert len(services.feedback_store.list()) == 1
    assert neighbor.graph_store.list() == [item for item in neighbor.graph_store.list()]
    assert len(neighbor.graph_store.list()) == 1
