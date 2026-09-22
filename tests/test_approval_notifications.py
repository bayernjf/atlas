# -*- coding: utf-8 -*-
"""T2 审批挂起邮件通知测试（docs/35 §2，D20 邮件子集）。

覆盖：EmailApprovalNotifier 纯文本邮件形状；DSL 编译期 notifyEmails 校验；
run_graph 登记审批后旁路触发通知（静态/插值/去重）、fail-safe 不阻断图、
无 notifier/无收件人不报错；旧图无字段完全兼容。
"""

from __future__ import annotations

import pytest

from atlas.collaboration.approvals import ApprovalBroker
from atlas.collaboration.notifications import EmailApprovalNotifier
from atlas.graph.dsl import GraphValidationError, parse_graph
from atlas.graph.loader import run_graph


def _approval_graph(notify_emails=None):
    config = {
        "summary": "订单 {{trigger-1.context.payload.order_id}} 退款审批",
        "approver": "客服主管",
        "timeoutSeconds": 10,
        "onTimeout": "reject",
        "approvedTarget": "tool-approve",
        "rejectedTarget": "tool-reject",
    }
    if notify_emails is not None:
        config["notifyEmails"] = notify_emails
    return {
        "version": 1,
        "variables": [],
        "nodes": [
            {"id": "trigger-1", "type": "trigger", "name": "t",
             "config": {"triggerType": "webhook", "webhookUrl": "/hooks/refund"}},
            {"id": "human-1", "type": "human_approval", "name": "人工审批", "config": config},
            {"id": "tool-approve", "type": "tool_call", "name": "通过侧",
             "config": {"tool": "op-approve"}},
            {"id": "tool-reject", "type": "tool_call", "name": "拒绝侧",
             "config": {"tool": "op-reject"}},
        ],
        "edges": [
            {"id": "e1", "source": "trigger-1", "target": "human-1"},
            {"id": "e2", "source": "human-1", "target": "tool-approve"},
            {"id": "e3", "source": "human-1", "target": "tool-reject"},
        ],
    }


class FakeMessages:
    def __init__(self):
        self.sent = []

    def send(self, channel, to, subject, body):
        self.sent.append({"channel": channel, "to": to, "subject": subject, "body": body})
        return {"delivered": "smtp"}


class FakeNotifier:
    def __init__(self, exc: Exception | None = None):
        self.calls = []
        self.exc = exc

    def notify_pending(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc is not None:
            raise self.exc


def _run_and_capture_approval(graph_dict, notifier, payload=None):
    graph = parse_graph(graph_dict)
    payload = payload or {"order_id": "O-1", "owner_email": "owner@example.com"}
    payload = {**payload, "approvals": {"human-1": "approved"}}
    starts = []

    def emit(event):
        if event.get("type") == "node_start" and event.get("approval"):
            starts.append(event["approval"])

    result = run_graph(
        graph,
        inputs=payload,
        approval_broker=ApprovalBroker(),
        approval_notifier=notifier,
        emit=emit,
        tracer=None,
    )
    return result, (starts[0] if starts else None)


# ============================ EmailApprovalNotifier ============================


def test_email_notifier_renders_plaintext_email():
    msgs = FakeMessages()
    notifier = EmailApprovalNotifier(msgs, "http://app.example.com/")
    notifier.notify_pending(
        graph_id="g1",
        node_id="human-1",
        token="tok-secret",
        summary="订单 O-1 退款审批",
        approver="客服主管",
        timeout_seconds=120,
        recipients=["a@example.com", "b@example.com"],
    )
    assert len(msgs.sent) == 1
    mail = msgs.sent[0]
    assert mail["channel"] == "email"
    assert mail["to"] == ["a@example.com", "b@example.com"]
    assert "订单 O-1 退款审批" in mail["subject"]
    body = mail["body"]
    assert "订单 O-1 退款审批" in body
    assert "客服主管" in body
    assert "120" in body
    assert "human-1" in body and "g1" in body
    # 入口 URL 去掉尾部斜杠；邮件只给入口、不含一键决策链接/token
    assert "http://app.example.com" in body
    assert "http://app.example.com/" not in body
    assert "tok-secret" not in body


def test_email_notifier_without_approver_omits_line():
    msgs = FakeMessages()
    notifier = EmailApprovalNotifier(msgs, "http://app.example.com")
    notifier.notify_pending(
        graph_id="g", node_id="n", token="t", summary="s", approver="",
        timeout_seconds=10, recipients=["a@example.com"],
    )
    assert "指定审批人" not in msgs.sent[0]["body"]


# ============================ DSL 编译期校验 ============================


def test_dsl_reject_notify_emails_not_array():
    with pytest.raises(GraphValidationError) as ei:
        parse_graph(_approval_graph(notify_emails="ops@example.com"))
    assert any("notifyEmails" in e for e in ei.value.errors)


def test_dsl_reject_too_many_notify_emails():
    emails = [f"op{i}@example.com" for i in range(6)]
    with pytest.raises(GraphValidationError) as ei:
        parse_graph(_approval_graph(notify_emails=emails))
    assert any("最多 5" in e for e in ei.value.errors)


def test_dsl_reject_static_email_without_at():
    with pytest.raises(GraphValidationError) as ei:
        parse_graph(_approval_graph(notify_emails=["not-an-email"]))
    assert any("notifyEmails" in e for e in ei.value.errors)


def test_dsl_allow_placeholder_email_and_legacy_graph():
    # 含插值占位符的项编译期无法判定，放行（运行时过滤）
    parse_graph(_approval_graph(notify_emails=["{{trigger-1.context.payload.owner_email}}"]))
    # 旧图无 notifyEmails 字段：完全兼容
    parse_graph(_approval_graph())


# ============================ run_graph 通知触发 ============================


def test_run_notifies_static_recipients_with_notified_payload():
    notifier = FakeNotifier()
    result, approval = _run_and_capture_approval(
        _approval_graph(notify_emails=["ops@example.com"]), notifier
    )
    assert result["status"] == "completed"
    assert approval is not None
    assert approval["notified"] is True
    assert "notifyError" not in approval
    assert len(notifier.calls) == 1
    call = notifier.calls[0]
    assert call["recipients"] == ["ops@example.com"]
    assert call["node_id"] == "human-1"
    assert call["timeout_seconds"] == 10
    assert "O-1" in call["summary"]  # summary 已插值


def test_run_interpolates_and_dedupes_recipients():
    notifier = FakeNotifier()
    _, approval = _run_and_capture_approval(
        _approval_graph(notify_emails=[
            "{{trigger-1.context.payload.owner_email}}",
            "{{trigger-1.context.payload.owner_email}}",
            "ops@example.com",
        ]),
        notifier,
    )
    assert approval["notified"] is True
    assert notifier.calls[0]["recipients"] == ["owner@example.com", "ops@example.com"]


def test_run_notification_failure_does_not_block_graph():
    notifier = FakeNotifier(exc=RuntimeError("smtp down"))
    result, approval = _run_and_capture_approval(
        _approval_graph(notify_emails=["ops@example.com"]), notifier
    )
    # fail-safe：图照常完成，载荷标 notified=False + notifyError
    assert result["status"] == "completed"
    assert approval["notified"] is False
    assert "smtp down" in approval["notifyError"]


def test_run_without_notifier_marks_notified_false():
    graph = parse_graph(_approval_graph(notify_emails=["ops@example.com"]))
    starts = []
    result = run_graph(
        graph,
        inputs={"order_id": "O-1", "approvals": {"human-1": "approved"}},
        approval_broker=ApprovalBroker(),
        approval_notifier=None,
        emit=lambda e: starts.append(e) if e.get("approval") else None,
        tracer=None,
    )
    assert result["status"] == "completed"
    approval = next(e["approval"] for e in starts if e.get("approval"))
    assert approval["notified"] is False
    assert "notifyError" not in approval


def test_run_drops_interpolated_recipient_without_at():
    notifier = FakeNotifier()
    # 缺失路径：占位符原样保留且无 @，运行时丢弃 → 无收件人、不调用通知
    _, approval = _run_and_capture_approval(
        _approval_graph(notify_emails=["{{trigger-1.context.payload.missing}}"]),
        notifier,
        payload={"order_id": "O-1"},
    )
    assert notifier.calls == []
    assert approval["notified"] is False
    assert "notifyError" not in approval
