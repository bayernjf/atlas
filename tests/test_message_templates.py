# -*- coding: utf-8 -*-
"""消息模板系统 v1（打包 A2，docs/98；U1189–U1192）。

覆盖：模板实体 CRUD＋形状校验＋kind 过滤＋跨租户隔离（U1189/U1190）；
消费接线·审批（U1191）与告警（U1192）：配模板走模板渲染、未配置回退默认正文逐字不变。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from atlas.api.main import app
from atlas.collaboration.notifications import EmailApprovalNotifier
from atlas.iam.deps import session_store
from atlas.iam.principals import authenticate
from atlas.message.template_store import (
    MessageTemplateNameConflict,
    MessageTemplateStore,
    extract_placeholders,
    validate_message_template,
)
from atlas.monitoring.notify import AlertNotifier, build_alert_body, build_alert_subject

client = TestClient(app)


@pytest.fixture(autouse=True)
def _admin_a_session():
    principal = authenticate("admin-a", "admin123")
    assert principal is not None
    token = session_store.issue(principal)
    client.headers["Authorization"] = f"Bearer {token}"
    try:
        yield
    finally:
        client.headers.pop("authorization", None)
        session_store.revoke(token)


def _auth(username: str) -> dict[str, str]:
    principal = authenticate(
        username,
        "admin123" if username.startswith("admin") else f"{username.split('-')[0]}123",
    )
    assert principal is not None
    return {"Authorization": f"Bearer {session_store.issue(principal)}"}


def _sample_payload(**overrides) -> dict:
    payload = {
        "name": "审批提醒",
        "kind": "approval",
        "subject": "[Atlas] 审批待处理：{{title}}",
        "body": "节点 {{node_id}}\n说明 {{summary}}\n指定 {{approver}}",
        "variables": ["title", "node_id", "summary", "approver"],
    }
    payload.update(overrides)
    return payload


# --- 校验纯逻辑 -----------------------------------------------------------

def test_validate_message_template_shape_rules():
    # 合法声明通过。
    assert validate_message_template(
        name="t", kind="approval", subject="s {{a}}", body="b {{a}}", variables=["a"]
    ) == []
    # kind 非法。
    errors = validate_message_template(
        name="t", kind="sms", subject="s", body="b", variables=[]
    )
    assert any("approval 或 alert" in e for e in errors)
    # subject/body 空。
    errors = validate_message_template(
        name="t", kind="alert", subject="", body="b", variables=[]
    )
    assert any("主题不能为空" in e for e in errors)
    errors = validate_message_template(
        name="t", kind="alert", subject="s", body="", variables=[]
    )
    assert any("正文不能为空" in e for e in errors)
    # variables 白名单外/重复/超量。
    errors = validate_message_template(
        name="t", kind="alert", subject="s", body="b", variables=["1bad", "ok", "ok"]
    )
    assert any("不合法" in e for e in errors)
    assert any("重复" in e for e in errors)
    errors = validate_message_template(
        name="t", kind="alert", subject="s", body="b", variables=list(range(21))
    )
    assert any("不能超过 20 个" in e for e in errors)
    # 未声明占位（U1189：422 MESSAGE_TEMPLATE_UNDECLARED_VAR 识别）。
    errors = validate_message_template(
        name="t", kind="approval", subject="s {{a}}", body="b {{undeclared}}", variables=["a"]
    )
    assert any("未声明变量 undeclared" in e for e in errors)


def test_extract_placeholders_dedup_and_order():
    assert extract_placeholders("{{a}} x {{ b }} {{a}}") == ["a", "b"]
    assert extract_placeholders("无占位") == []


# --- U1189：内存 store CRUD + name 冲突 ------------------------------------

def test_message_template_store_crud():
    store = MessageTemplateStore()
    first = store.add(**_sample_payload())
    assert first.id == "mtpl-1"
    assert first.created_at and first.updated_at == first.created_at
    second = store.add(
        name="告警提醒", kind="alert", subject="[Atlas告警] {{title}}",
        body="图 {{graph_id}} 运行 {{run_id}}", variables=["title", "graph_id", "run_id"],
    )
    assert second.id == "mtpl-2"
    # list 新在前。
    assert [item.id for item in store.list()] == ["mtpl-2", "mtpl-1"]
    # kind 过滤。
    assert [item.id for item in store.list("alert")] == ["mtpl-2"]
    assert [item.id for item in store.list("approval")] == ["mtpl-1"]
    # get / update / delete。
    updated = store.update(first.id, name=first.name, kind="approval",
                           subject="新 {{title}}", body="新 {{title}}", variables=["title"])
    assert updated is not None and updated.subject == "新 {{title}}"
    assert updated.updated_at != updated.created_at
    assert store.get(first.id).subject == "新 {{title}}"
    assert store.delete(first.id) is True
    assert store.get(first.id) is None
    assert store.delete(first.id) is False
    # clear 重置 seq。
    store.clear()
    assert store.add(**_sample_payload()).id == "mtpl-1"


def test_message_template_name_conflict_raise():
    store = MessageTemplateStore()
    store.add(**_sample_payload())
    with pytest.raises(MessageTemplateNameConflict):
        store.add(name="审批提醒", kind="approval", subject="s", body="b", variables=[])
    other = store.add(
        name="告警提醒", kind="alert", subject="s", body="b", variables=[]
    )
    # update 撞名（大小写不敏感）。
    with pytest.raises(MessageTemplateNameConflict):
        store.update(other.id, name="审批提醒", kind="alert", subject="s", body="b", variables=[])
    # 同名自更新通过（大小写变化视为同一模板）。
    renamed = store.update(other.id, name="告警提醒", kind="alert", subject="s", body="b", variables=[])
    assert renamed is not None


# --- U1189：REST CRUD ------------------------------------------------------

def test_api_message_template_crud_and_validation():
    # 创建 201。
    resp = client.post("/api/message-templates", json=_sample_payload())
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["id"] == "mtpl-1"
    assert body["variables"] == ["title", "node_id", "summary", "approver"]
    # 重名 409。
    resp = client.post("/api/message-templates", json=_sample_payload(name="审批提醒"))
    assert resp.status_code == 409, resp.text
    # 坏形状 422 且模板不变（未声明占位）。
    resp = client.post(
        "/api/message-templates",
        json=_sample_payload(name="坏模板", body="正文 {{ghost}}", variables=["title"]),
    )
    assert resp.status_code == 422, resp.text
    assert "未声明变量 ghost" in resp.json()["detail"]
    assert client.get("/api/message-templates").json()["items"][0]["name"] == "审批提醒"
    # 列表投影不含 body。
    items = client.get("/api/message-templates").json()["items"]
    assert items and "body" not in items[0]
    assert items[0]["subject"] == "[Atlas] 审批待处理：{{title}}"
    # 详情含 body。
    detail = client.get(f"/api/message-templates/{body['id']}").json()
    assert "body" in detail and "{{node_id}}" in detail["body"]
    # PUT 全量更新。
    resp = client.put(
        f"/api/message-templates/{body['id']}",
        json=_sample_payload(name="审批提醒改", body="新正文 {{title}}", variables=["title"]),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "审批提醒改"
    assert resp.json()["id"] == body["id"]
    # PUT 撞名 409。
    client.post("/api/message-templates",
                json=_sample_payload(name="告警提醒", kind="alert", subject="s {{title}}", body="b {{title}}", variables=["title"]))
    resp = client.put(
        f"/api/message-templates/{body['id']}",
        json=_sample_payload(name="告警提醒", subject="s", body="b", variables=[]),
    )
    assert resp.status_code == 409, resp.text
    # PUT 未知 404。
    resp = client.put(
        "/api/message-templates/mtpl-999",
        json=_sample_payload(name="无主", subject="s", body="b", variables=[]),
    )
    assert resp.status_code == 404, resp.text
    # DELETE。
    resp = client.delete(f"/api/message-templates/{body['id']}")
    assert resp.status_code == 200 and resp.json()["deleted"] is True
    assert client.get(f"/api/message-templates/{body['id']}").status_code == 404
    assert client.delete(f"/api/message-templates/{body['id']}").status_code == 404


# --- U1190：kind 过滤 + 跨租户隔离 ------------------------------------------

def test_api_kind_filter_and_tenant_isolation():
    client.post("/api/message-templates", json=_sample_payload())
    client.post("/api/message-templates",
                json=_sample_payload(name="告警提醒", kind="alert", subject="s {{title}}", body="b {{title}}", variables=["title"]))
    approvals = client.get("/api/message-templates?kind=approval").json()["items"]
    alerts = client.get("/api/message-templates?kind=alert").json()["items"]
    assert len(approvals) == 1 and approvals[0]["name"] == "审批提醒"
    assert len(alerts) == 1 and alerts[0]["name"] == "告警提醒"
    # 跨租户：admin-b 看不到、读不到、删不掉。
    other = _auth("admin-b")
    assert client.get("/api/message-templates", headers=other).json()["items"] == []
    tid = approvals[0]["id"]
    assert client.get(f"/api/message-templates/{tid}", headers=other).status_code == 404
    assert client.delete(f"/api/message-templates/{tid}", headers=other).status_code == 404


# --- U1191：消费接线·审批（配模板走模板；未配置回退默认逐字不变） --------------

class _FakeMessages:
    """记录 send 调用；delivered 字段可注入（docs/77 R6 语义）。"""

    def __init__(self, delivered: str = "smtp") -> None:
        self.calls: list[tuple] = []
        self._delivered = delivered

    def send(self, channel: str, recipients: list[str], subject: str, body: str, **kwargs):
        self.calls.append((channel, list(recipients), subject, body))
        return {"delivered": self._delivered}


def test_approval_notifier_renders_template_when_configured():
    messages = _FakeMessages()
    store = MessageTemplateStore()
    store.add(
        name="审批提醒", kind="approval",
        subject="[Atlas] {{title}} 请审批",
        body="节点：{{node_id}}\n说明：{{summary}}\n指定：{{approver}}\n链接：{{decision_url}}",
        variables=["title", "node_id", "summary", "approver", "decision_url"],
    )
    notifier = EmailApprovalNotifier(messages, public_url="https://atlas.example", template_store=store)
    notifier.notify_pending(
        graph_id="g-1", node_id="human-1", token="tok-1", summary="退款审批",
        approver="ops@corp.io", timeout_seconds=3600, recipients=["ops@corp.io"],
    )
    assert len(messages.calls) == 1
    _, recipients, subject, body = messages.calls[0]
    assert subject == "[Atlas] 退款审批 请审批"
    assert "节点：human-1" in body
    assert "说明：退款审批" in body
    assert "指定：ops@corp.io" in body
    assert "链接：https://atlas.example/approvals/" in body
    assert recipients == ["ops@corp.io"]


def test_approval_notifier_falls_back_to_default_when_no_template():
    messages = _FakeMessages()
    store = MessageTemplateStore()
    notifier = EmailApprovalNotifier(messages, public_url="https://atlas.example", template_store=store)
    notifier.notify_pending(
        graph_id="g-1", node_id="human-1", token="tok-2", summary="退款审批",
        approver="ops@corp.io", timeout_seconds=3600, recipients=["ops@corp.io"],
    )
    assert len(messages.calls) == 1
    _, _, subject, body = messages.calls[0]
    # 默认正文逐字不变（与未接线基线一致：subject 前缀 + 既有行）。
    assert subject == "[Atlas] 审批待处理：退款审批"
    assert "有一笔人机审批正在等待处理。" in body
    assert "审批节点：human-1（图 g-1）" in body
    assert "指定审批人：ops@corp.io" in body


# --- U1192：消费接线·告警（模板渲染；未配置回退 build_*） ----------------------

class _FakeAlert:
    rule_name = "延迟过高"
    rule_id = "r-1"
    severity = "critical"
    graph_id = "g-1"
    last_run_id = "run-1"
    message = "P99 超阈值"
    assignee = "ops"
    first_seen = "2026-10-07T00:00:00Z"
    id = "a-1"
    status = "firing"
    count = 3
    last_seen = "2026-10-07T00:00:01Z"


def test_alert_notifier_renders_template_when_configured():
    messages = _FakeMessages()
    store = MessageTemplateStore()
    store.add(
        name="告警提醒", kind="alert",
        subject="[Atlas告警] {{severity}} {{title}}",
        body="消息：{{message}}\n图：{{graph_id}}\n运行：{{run_id}}",
        variables=["title", "severity", "graph_id", "run_id", "message", "transition"],
    )
    notifier = AlertNotifier(messages, template_store=store)
    from atlas.monitoring.notify import AlertChannel
    cfg = AlertChannel(channel="webhook", to="https://hooks.example/x", enabled=True)
    delivery = notifier.notify(_FakeAlert(), cfg)
    assert delivery.errorCode is None or delivery.errorCode == ""
    assert len(messages.calls) == 1
    _, _, subject, body = messages.calls[0]
    assert subject == "[Atlas告警] critical 延迟过高"
    assert "消息：P99 超阈值" in body
    assert "图：g-1" in body and "运行：run-1" in body


def test_alert_notifier_falls_back_to_default_when_no_template():
    messages = _FakeMessages()
    notifier = AlertNotifier(messages)
    from atlas.monitoring.notify import AlertChannel
    cfg = AlertChannel(channel="webhook", to="https://hooks.example/x", enabled=True)
    alert = _FakeAlert()
    notifier.notify(alert, cfg)
    assert len(messages.calls) == 1
    _, _, subject, body = messages.calls[0]
    assert subject == build_alert_subject(alert)
    assert body == build_alert_body(alert)


def test_alert_notifier_lifecycle_renders_template():
    messages = _FakeMessages()
    store = MessageTemplateStore()
    store.add(
        name="告警提醒", kind="alert",
        subject="[Atlas告警] {{transition}} {{title}}",
        body="状态：{{status}}\n图：{{graph_id}}",
        variables=["title", "severity", "graph_id", "run_id", "message", "transition", "status"],
    )
    notifier = AlertNotifier(messages, template_store=store)
    from atlas.monitoring.notify import AlertChannel
    cfg = AlertChannel(channel="webhook", to="https://hooks.example/x", enabled=True)
    delivery = notifier.notify_lifecycle(_FakeAlert(), cfg, transition="resolved")
    assert delivery.lastNotifiedAt
    assert len(messages.calls) == 1
    _, _, subject, body = messages.calls[0]
    # transition 原样注入；title＝LIFECYCLE_TITLES['resolved']。
    assert subject == "[Atlas告警] resolved 告警已解决"
    assert "图：g-1" in body
