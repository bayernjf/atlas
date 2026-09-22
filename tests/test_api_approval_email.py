# -*- coding: utf-8 -*-
"""邮件深链公开端点测试（docs/36 §3）。

覆盖 email-view / email-decision：无需登录；坏/过期 token、未知租户、
未知审批、租户不符统一 404 不区分；只读幂等；同意/拒绝/卡片 actionId
落 broker；重复 409、缺 decision 422；成功写 email-link 审计且不泄漏
token/comment。peek 不触发租户惰性创建。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from atlas.api.main import _email_token_issuer
from atlas.api.main import app
from atlas.collaboration.email_token import TokenIssuer
from atlas.iam.deps import tenant_registry

client = TestClient(app)

T1 = "t1"
T2 = "t2"


def _request_pending(tenant: str, *, card_template_id=None, card_context=None,
                      timeout: int = 300) -> str:
    broker = tenant_registry.get(tenant).approval_broker
    return broker.request(
        node_id="human-1",
        graph_id="g1",
        summary="订单 C-9 退款审批",
        approver="客服主管",
        timeout_seconds=timeout,
        card_template_id=card_template_id,
        card_context=card_context,
    )


def _sign(tenant: str, approval_token: str, timeout: int = 300) -> str:
    return _email_token_issuer.issue(tenant, approval_token, timeout)


class PastClock:
    def __call__(self) -> float:
        return 1000.0


# ============================ email-view ============================


def test_email_view_pending_shape_without_login():
    approval_token = _request_pending(T1)
    signed = _sign(T1, approval_token)
    resp = client.get("/api/approvals/email-view", params={"token": signed})
    assert resp.status_code == 200
    view = resp.json()
    assert view["status"] == "pending"
    assert view["summary"] == "订单 C-9 退款审批"
    assert view["nodeId"] == "human-1"
    assert view["graphId"] == "g1"
    assert view["approver"] == "客服主管"
    assert view["timeoutSeconds"] == 300
    assert view["createdAt"] > 0
    assert 0 <= view["remainingSeconds"] <= 300
    assert "card" not in view
    assert "decision" not in view


def test_email_view_is_read_only_and_idempotent():
    approval_token = _request_pending(T1)
    signed = _sign(T1, approval_token)
    for _ in range(3):
        assert client.get("/api/approvals/email-view", params={"token": signed}).status_code == 200
    broker = tenant_registry.get(T1).approval_broker
    assert broker.get(approval_token)["decision"] is None


def test_email_view_garbage_token_404():
    resp = client.get("/api/approvals/email-view", params={"token": "not-a-token"})
    assert resp.status_code == 404
    assert resp.json()["detail"] == "审批链接无效或已过期"


def test_email_view_expired_token_404():
    approval_token = _request_pending(T1)
    past_issuer = TokenIssuer(secret=_email_token_issuer.secret, clock=PastClock())
    signed = past_issuer.issue(T1, approval_token, 300)
    resp = client.get("/api/approvals/email-view", params={"token": signed})
    assert resp.status_code == 404


def test_email_view_unknown_tenant_does_not_assemble():
    signed = _sign("ghost-tenant", "whatever")
    resp = client.get("/api/approvals/email-view", params={"token": signed})
    assert resp.status_code == 404
    assert tenant_registry.peek("ghost-tenant") is None


def test_email_view_unknown_approval_404():
    signed = _sign(T1, "missing-approval-token")
    resp = client.get("/api/approvals/email-view", params={"token": signed})
    assert resp.status_code == 404


def test_email_view_tenant_mismatch_404():
    # 审批属于 t1；token 签名绑定 t2（t2 已装配），peek t2 取不到 → 404
    tenant_registry.get(T2)
    approval_token = _request_pending(T1)
    signed = _sign(T2, approval_token)
    resp = client.get("/api/approvals/email-view", params={"token": signed})
    assert resp.status_code == 404


def test_email_view_resolved_shows_final_state():
    approval_token = _request_pending(T1)
    broker = tenant_registry.get(T1).approval_broker
    broker.resolve(approval_token, "rejected", resolved_by="human")
    signed = _sign(T1, approval_token)
    view = client.get("/api/approvals/email-view", params={"token": signed}).json()
    assert view["status"] == "resolved"
    assert view["decision"] == "rejected"
    assert view["resolvedBy"] == "human"


def test_email_view_includes_email_card_when_configured():
    card_context = {
        "global": {"approval_limit": 500},
        "trigger-1": {"context": {"payload": {
            "order_id": "C-9", "amount": 99, "reason": "商品破损"}}},
        "ai_decision-1": {"decision": {"reason": "建议通过"}},
    }
    approval_token = _request_pending(
        T1, card_template_id="refund-approval", card_context=card_context
    )
    signed = _sign(T1, approval_token)
    view = client.get("/api/approvals/email-view", params={"token": signed}).json()
    assert view["card"]["channel"] == "email"
    assert "退款审批卡片" in view["card"]["subject"]
    assert {link["id"] for link in view["card"]["links"]} == {"approve", "reject"}


# ============================ email-decision ============================


def test_email_decision_approve_without_login():
    approval_token = _request_pending(T1)
    signed = _sign(T1, approval_token)
    resp = client.post("/api/approvals/email-decision", json={"token": signed})
    assert resp.status_code == 422  # 缺 decision/actionId

    resp = client.post(
        "/api/approvals/email-decision", json={"token": signed, "decision": "approved"}
    )
    assert resp.status_code == 200
    assert resp.json()["decision"] == "approved"
    broker = tenant_registry.get(T1).approval_broker
    assert broker.get(approval_token)["decision"] == "approved"


def test_email_decision_reject_with_comment():
    approval_token = _request_pending(T1)
    signed = _sign(T1, approval_token)
    resp = client.post(
        "/api/approvals/email-decision",
        json={"token": signed, "decision": "rejected", "comment": "证据不足"},
    )
    assert resp.status_code == 200
    assert resp.json()["decision"] == "rejected"
    assert broker_get(T1, approval_token)["decision"] == "rejected"


def broker_get(tenant: str, approval_token: str) -> dict:
    return tenant_registry.get(tenant).approval_broker.get(approval_token)


def test_email_decision_duplicate_409():
    approval_token = _request_pending(T1)
    signed = _sign(T1, approval_token)
    body = {"token": signed, "decision": "approved"}
    assert client.post("/api/approvals/email-decision", json=body).status_code == 200
    resp = client.post("/api/approvals/email-decision", json=body)
    assert resp.status_code == 409


def test_email_decision_bad_token_404():
    resp = client.post(
        "/api/approvals/email-decision",
        json={"token": "garbage", "decision": "approved"},
    )
    assert resp.status_code == 404


def test_email_decision_card_action_maps_output():
    card_context = {
        "global": {"approval_limit": 500},
        "trigger-1": {"context": {"payload": {
            "order_id": "C-9", "amount": 99, "reason": "破损"}}},
    }
    approval_token = _request_pending(
        T1, card_template_id="refund-approval", card_context=card_context
    )
    signed = _sign(T1, approval_token)
    resp = client.post(
        "/api/approvals/email-decision",
        json={"token": signed, "actionId": "approve"},
    )
    assert resp.status_code == 200
    assert resp.json()["decision"] == "approved"
    assert resp.json()["actionId"] == "approve"


def test_email_decision_card_unknown_action_422():
    approval_token = _request_pending(
        T1, card_template_id="refund-approval", card_context={"global": {}}
    )
    signed = _sign(T1, approval_token)
    resp = client.post(
        "/api/approvals/email-decision",
        json={"token": signed, "actionId": "explode"},
    )
    assert resp.status_code == 422


def test_email_decision_writes_email_link_audit_without_leaks():
    approval_token = _request_pending(T1)
    signed = _sign(T1, approval_token)
    client.post(
        "/api/approvals/email-decision",
        json={"token": signed, "decision": "approved", "comment": "机密备注"},
    )
    entries = [
        e for e in tenant_registry.get(T1).audit_store.list()
        if e["action"].startswith("approval.email_decision")
    ]
    assert entries  # 同模块前序用例也写审计；检查最新一条
    entry = entries[-1]
    assert entry["actor"] == "email-link"
    assert entry["action"] == "approval.email_decision:approved"
    serialized = str(entry)
    assert signed not in serialized
    assert approval_token not in serialized
    assert "机密备注" not in serialized
