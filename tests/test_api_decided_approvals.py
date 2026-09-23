# -*- coding: utf-8 -*-
"""GET /api/approvals/decided 与决策结果邮件 API 触发测试（docs/37 §3/§4）。

覆盖：三角色可读、跨租户隔离、limit clamp/回显、401；人工/邮件链接决策后
结果邮件各触发一次（收件人取挂起时留存的 notify_recipients），通知异常
不阻断决策；重复决策不二次通知。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from atlas.api.main import _email_token_issuer
from atlas.api.main import app
from atlas.iam.deps import session_store, tenant_registry

anon = TestClient(app)


def _token(username: str, password: str) -> str:
    from atlas.iam.principals import authenticate

    principal = authenticate(username, password)
    assert principal is not None
    return session_store.issue(principal)


def _auth(username: str, password: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(username, password)}"}


ADMIN_A = _auth("admin-a", "admin123")
OPERATOR_A = _auth("operator-a", "operator123")
VIEWER_A = _auth("viewer-a", "viewer123")
ADMIN_B = _auth("admin-b", "admin123")


def _request(tenant: str, *, node_id: str = "human-1",
             notify_recipients=None) -> str:
    broker = tenant_registry.get(tenant).approval_broker
    return broker.request(
        node_id=node_id,
        graph_id="g1",
        summary="订单 C-1 退款审批",
        approver="客服主管",
        timeout_seconds=300,
        notify_recipients=notify_recipients,
    )


def test_decided_requires_login():
    assert anon.get("/api/approvals/decided").status_code == 401


def test_decided_readable_by_all_roles_and_scoped_per_tenant():
    token_a = _request("t1", node_id="human-a")
    tenant_registry.get("t1").approval_broker.resolve(token_a, "approved", comment="ok")
    for headers in (ADMIN_A, OPERATOR_A, VIEWER_A):
        resp = anon.get("/api/approvals/decided", headers=headers)
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert any(item["token"] == token_a for item in items)

    token_b = _request("t2", node_id="human-b")
    tenant_registry.get("t2").approval_broker.resolve(token_b, "rejected")
    items_b = anon.get("/api/approvals/decided", headers=ADMIN_B).json()["items"]
    tokens_b = {item["token"] for item in items_b}
    assert token_b in tokens_b and token_a not in tokens_b


def test_decided_excludes_pending_and_clamps_limit():
    pending_token = _request("t1", node_id="human-pending")
    resp = anon.get("/api/approvals/decided", headers=VIEWER_A, params={"limit": 0})
    assert resp.status_code == 200
    body = resp.json()
    assert body["limit"] == 1
    assert all(item["token"] != pending_token for item in body["items"])

    resp = anon.get("/api/approvals/decided", headers=VIEWER_A, params={"limit": 1000})
    assert resp.json()["limit"] == 200


def test_decided_shape_fields():
    token = _request("t1", node_id="human-shape")
    tenant_registry.get("t1").approval_broker.resolve(
        token, "rejected", comment="证据不足"
    )
    item = next(
        item for item in anon.get("/api/approvals/decided", headers=ADMIN_A).json()["items"]
        if item["token"] == token
    )
    assert item["node_id"] == "human-shape"
    assert item["graph_id"] == "g1"
    assert item["decision"] == "rejected"
    assert item["resolvedBy"] == "human"
    assert item["comment"] == "证据不足"
    assert item["createdAt"] > 0
    assert "card_context" not in item


def test_human_decision_sends_result_email_once():
    token = _request("t1", notify_recipients=["ops@example.com"])
    resp = anon.post(
        f"/api/approvals/{token}/decision",
        headers=OPERATOR_A,
        json={"decision": "approved"},
    )
    assert resp.status_code == 200
    messages = tenant_registry.get("t1").message_service.list()
    result_mails = [m for m in messages if "审批已处理" in m.get("subject", "")]
    assert len(result_mails) == 1
    mail = result_mails[-1]
    assert mail["to"] == ["ops@example.com"]
    assert "/approvals/" not in mail["body"]

    # 重复决策 409 且不二次通知
    resp = anon.post(
        f"/api/approvals/{token}/decision",
        headers=OPERATOR_A,
        json={"decision": "rejected"},
    )
    assert resp.status_code == 409
    messages = tenant_registry.get("t1").message_service.list()
    assert sum(1 for m in messages if "审批已处理" in m.get("subject", "")) == 1


def test_email_link_decision_sends_result_email():
    token = _request("t1", notify_recipients=["ops@example.com"])
    signed = _email_token_issuer.issue("t1", token, 300)
    resp = anon.post(
        "/api/approvals/email-decision",
        json={"token": signed, "decision": "rejected", "comment": "材料不全"},
    )
    assert resp.status_code == 200
    mails = [m for m in tenant_registry.get("t1").message_service.list()
             if "审批已处理" in m.get("subject", "")]
    mail = mails[-1]
    assert mail["to"] == ["ops@example.com"]
    assert "拒绝" in mail["body"] and "邮件链接处理" in mail["body"]


def test_result_notification_failure_does_not_block_decision():
    token = _request("t1", notify_recipients=["ops@example.com"])

    class Boom:
        def send(self, *args, **kwargs):
            raise RuntimeError("mailer down")

    services = tenant_registry.get("t1")
    original = services.message_service
    try:
        services.message_service = Boom()
        resp = anon.post(
            f"/api/approvals/{token}/decision",
            headers=ADMIN_A,
            json={"decision": "approved"},
        )
        assert resp.status_code == 200
        assert services.approval_broker.get(token)["decision"] == "approved"
    finally:
        services.message_service = original
