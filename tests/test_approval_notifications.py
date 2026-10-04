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
    def __init__(self, exc: Exception | None = None, returns: bool = True):
        self.calls = []
        self.decided_calls = []
        self.exc = exc
        self.returns = returns

    def notify_pending(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc is not None:
            raise self.exc
        return self.returns

    def notify_decided(self, **kwargs):
        self.decided_calls.append(kwargs)


class FakeIssuer:
    def __init__(self, signed: str = "SIGNED-TOKEN", exc: Exception | None = None):
        self.signed = signed
        self.exc = exc
        self.calls = []

    def issue(self, tenant_id, approval_token, timeout_seconds, recipient=None):
        self.calls.append((tenant_id, approval_token, timeout_seconds, recipient))
        if self.exc is not None:
            raise self.exc
        return self.signed


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
    issuer = FakeIssuer()
    notifier = EmailApprovalNotifier(
        msgs, "http://app.example.com/", tenant_id="t1", issuer=issuer
    )
    notifier.notify_pending(
        graph_id="g1",
        node_id="human-1",
        token="tok-secret",
        summary="订单 O-1 退款审批",
        approver="客服主管",
        timeout_seconds=120,
        recipients=["a@example.com", "b@example.com"],
    )
    # 签发入参：租户 + 原始审批 token + 超时秒 + 首收件人（docs/64 J-1b 绑定）
    assert issuer.calls == [("t1", "tok-secret", 120, "a@example.com")]
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
    # 入口 URL 去掉尾部斜杠；邮件附签名深链，原始审批 token 不直接出现
    assert "一键处理：http://app.example.com/approvals/SIGNED-TOKEN" in body
    assert "或前往应用：http://app.example.com" in body
    assert "tok-secret" not in body


def test_email_notifier_without_approver_omits_line():
    msgs = FakeMessages()
    notifier = EmailApprovalNotifier(
        msgs, "http://app.example.com", tenant_id="t", issuer=FakeIssuer()
    )
    notifier.notify_pending(
        graph_id="g", node_id="n", token="t", summary="s", approver="",
        timeout_seconds=10, recipients=["a@example.com"],
    )
    assert "指定审批人" not in msgs.sent[0]["body"]


def test_email_notifier_signing_failure_propagates():
    # 签名失败不上邮件、异常向上抛，由 graph 调用方 fail-safe 吞掉
    msgs = FakeMessages()
    notifier = EmailApprovalNotifier(
        msgs, "http://app.example.com", tenant_id="t",
        issuer=FakeIssuer(exc=RuntimeError("signer down")),
    )
    with pytest.raises(RuntimeError):
        notifier.notify_pending(
            graph_id="g", node_id="n", token="t", summary="s", approver="",
            timeout_seconds=10, recipients=["a@example.com"],
        )
    assert msgs.sent == []


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


# ============================ docs/37 决策结果邮件 ============================


def test_email_notifier_decided_renders_result_without_token():
    msgs = FakeMessages()
    notifier = EmailApprovalNotifier(
        msgs, "http://app.example.com", tenant_id="t1", issuer=FakeIssuer()
    )
    notifier.notify_decided(
        graph_id="g1",
        node_id="human-1",
        summary="订单 O-1 退款审批",
        decision="rejected",
        resolved_by="human",
        comment="材料不全",
        recipients=["ops@example.com"],
    )
    mail = msgs.sent[0]
    assert "审批已处理" in mail["subject"]
    body = mail["body"]
    assert "拒绝" in body and "人工处理" in body and "材料不全" in body
    assert "http://app.example.com" in body
    assert "/approvals/" not in body  # 结果邮件不含任何 token/决策链接


def test_email_notifier_decided_truncates_long_comment():
    msgs = FakeMessages()
    notifier = EmailApprovalNotifier(
        msgs, "http://app.example.com", tenant_id="t", issuer=FakeIssuer()
    )
    notifier.notify_decided(
        graph_id="g", node_id="n", summary="s", decision="approved",
        resolved_by="timeout", comment="", recipients=["a@example.com"],
    )
    body = msgs.sent[0]["body"]
    assert "同意" in body and "超时自动处理" in body
    assert "处理备注" not in body

    notifier.notify_decided(
        graph_id="g", node_id="n", summary="s", decision="approved",
        resolved_by="input", comment="X" * 500, recipients=["a@example.com"],
    )
    assert msgs.sent[1]["body"].count("X") == 200


def test_preset_input_triggers_decided_notification_once():
    notifier = FakeNotifier()
    _run_and_capture_approval(
        _approval_graph(notify_emails=["ops@example.com"]), notifier
    )
    assert len(notifier.decided_calls) == 1
    call = notifier.decided_calls[0]
    assert call["decision"] == "approved"
    assert call["resolved_by"] == "input"
    assert call["recipients"] == ["ops@example.com"]


def test_decided_notification_failure_does_not_block_graph():
    class FailingNotifier(FakeNotifier):
        def notify_decided(self, **kwargs):
            raise RuntimeError("mailer down")

    result, _ = _run_and_capture_approval(
        _approval_graph(notify_emails=["ops@example.com"]), FailingNotifier()
    )
    assert result["status"] == "completed"


# ============================ docs/77 R6 反向门 ============================
# "notified 只证没抛异常"是假阳性——必须读 delivered 字段


class DemoMessages(FakeMessages):
    """模拟未配 SMTP 的默认分支：delivered='in_process'"""

    def send(self, channel, to, subject, body):
        self.sent.append({"channel": channel, "to": to, "subject": subject, "body": body})
        return {"delivered": "in_process"}


def test_email_notifier_returns_false_for_in_process_delivery():
    """demo 回退 delivered='in_process' 不是真通知——R6 核心断言"""
    msgs = DemoMessages()
    notifier = EmailApprovalNotifier(
        msgs, "http://app.example.com", tenant_id="t", issuer=FakeIssuer()
    )
    result = notifier.notify_pending(
        graph_id="g", node_id="n", token="t", summary="s", approver="",
        timeout_seconds=10, recipients=["a@example.com"],
    )
    assert result is False


def test_email_notifier_delivered_field_alignment_with_messageservice():
    """docs/77 R6 关键验证：EmailApprovalNotifier.notify_pending 的 delivered 判断
    必须与 MessageService 实际写入 record['delivered'] 的值对齐。

    真实 MessageService 成功投递后设 record['delivered'] = "smtp"/"webhook"/...
    失败时抛异常；demo 回退时 delivered 保持初始值 "in_process"。
    所以 notifier 应该：delivered == "in_process" → False；其余非 None/非 None
    的渠道值 → True。
    """
    # 情况 A：demo 回退（未注入 sender）→ delivered="in_process" → False
    class InProcessMessages:
        def send(self, channel, to, subject, body):
            return {"delivered": "in_process"}  # 真实 MessageService demo 分支返回值

    notifier = EmailApprovalNotifier(
        InProcessMessages(), "http://app.example.com",
        tenant_id="t", issuer=FakeIssuer(),
    )
    assert notifier.notify_pending(
        graph_id="g", node_id="n", token="t", summary="s", approver="",
        timeout_seconds=10, recipients=["a@example.com"],
    ) is False

    # 情况 B：真实 SMTP 投递成功 → delivered="smtp" → True
    class SmtpMessages:
        def send(self, channel, to, subject, body):
            return {"delivered": "smtp"}  # 真实 MessageService 成功投递后的值

    notifier2 = EmailApprovalNotifier(
        SmtpMessages(), "http://app.example.com",
        tenant_id="t", issuer=FakeIssuer(),
    )
    assert notifier2.notify_pending(
        graph_id="g", node_id="n", token="t", summary="s", approver="",
        timeout_seconds=10, recipients=["a@example.com"],
    ) is True

    # 情况 C：webhook 投递成功 → delivered="webhook" → True
    class WebhookMessages:
        def send(self, channel, to, subject, body):
            return {"delivered": "webhook"}

    notifier3 = EmailApprovalNotifier(
        WebhookMessages(), "http://app.example.com",
        tenant_id="t", issuer=FakeIssuer(),
    )
    assert notifier3.notify_pending(
        graph_id="g", node_id="n", token="t", summary="s", approver="",
        timeout_seconds=10, recipients=["a@example.com"],
    ) is True

    # 情况 D：delivered 为 None（异常边界）→ False
    class NullDeliveredMessages:
        def send(self, channel, to, subject, body):
            return {"delivered": None}

    notifier4 = EmailApprovalNotifier(
        NullDeliveredMessages(), "http://app.example.com",
        tenant_id="t", issuer=FakeIssuer(),
    )
    assert notifier4.notify_pending(
        graph_id="g", node_id="n", token="t", summary="s", approver="",
        timeout_seconds=10, recipients=["a@example.com"],
    ) is False


def test_run_notifies_false_when_notifier_returns_false():
    """loader 必须用 notifier 的返回值设 notified，而不是只看有没有抛异常。"""
    # FakeNotifier(returns=False) 模拟 EmailApprovalNotifier 在 demo 回退时的行为
    notifier = FakeNotifier(returns=False)
    _, approval = _run_and_capture_approval(
        _approval_graph(notify_emails=["ops@example.com"]), notifier
    )
    # 没抛异常，但返回了 False → notified 应为 False
    assert approval["notified"] is False
    assert "notifyError" not in approval  # 没抛异常就不设 error


# --- U1135/U1136：prod 的入口地址还是本地缺省时，签名深链不寄出（docs/89 §15 A-6）---


def _notify(msgs, issuer, public_url):
    EmailApprovalNotifier(msgs, public_url, tenant_id="t1", issuer=issuer).notify_pending(
        graph_id="g1",
        node_id="human-1",
        token="tok-secret",
        summary="订单 O-1 退款审批",
        approver="客服主管",
        timeout_seconds=120,
        recipients=["a@example.com"],
    )
    return msgs.sent[0]["body"]


def test_u1135_prod_with_loopback_public_url_sends_no_capability_link(monkeypatch):
    """prod＋`ATLAS_PUBLIC_URL` 未配（回落到本地缺省）⇒ 邮件里既没有深链也没有签名 token。

    发往 `http://localhost:…` 的链接对收件人不可达，却把一枚 capability token 交给
    "本机任何监听者"——宁可不发链接，让正文把原委与修法写清楚。
    """
    monkeypatch.setenv("ATLAS_ENV", "prod")
    monkeypatch.delenv("ATLAS_PUBLIC_URL", raising=False)
    body = _notify(FakeMessages(), FakeIssuer(), "http://localhost:5174")
    assert "SIGNED-TOKEN" not in body and "tok-secret" not in body
    assert "ATLAS_PUBLIC_URL" in body and "一键处理链接本次未随邮件发出" in body


def test_u1136_prod_with_real_public_url_still_sends_link(monkeypatch):
    """判别对照：配了对外地址，prod 的一键深链行为逐字不变（上一条不是"prod 都不发链接"）。"""
    monkeypatch.setenv("ATLAS_ENV", "prod")
    body = _notify(FakeMessages(), FakeIssuer(), "https://atlas.example.com/")
    assert "一键处理：https://atlas.example.com/approvals/SIGNED-TOKEN" in body
    assert "或前往应用：https://atlas.example.com" in body


def test_u1136_dev_with_local_public_url_keeps_the_link(monkeypatch):
    """dev/test 形态零变化：本地深链本来就是给本机点的。"""
    monkeypatch.setenv("ATLAS_ENV", "dev")
    body = _notify(FakeMessages(), FakeIssuer(), "http://localhost:5174")
    assert "一键处理：http://localhost:5174/approvals/SIGNED-TOKEN" in body


# --- U1137：令牌进日志只留可读引用（docs/89 §15 A-8b）--------------------------


def test_u1137_token_ref_keeps_correlation_without_the_credential():
    from atlas.api.main import _token_ref

    assert _token_ref("ap-" + "z" * 30) == "ap-zzzzz…"          # 前 8 位＋省略号：够定位，不够使用
    assert _token_ref("short") == "short"                        # 短值不加长省略号
    assert _token_ref(None) == "<空令牌>" and _token_ref("") == "<空令牌>"


def test_u1137_notify_failure_logs_the_reference_not_the_token(caplog):
    """决策结果通知失败时的告警行：全量 token 不得出现，8 位引用必须在。

    正向对照是必要的——把整条日志删掉也能让这个断言成立，而排障恰恰需要那一行。
    """
    import logging

    from atlas.api.main import _apply_approval_decision

    class Broker:
        def resolve(self, token, decision, *, comment=None, action_id=None):
            return True

        def get_notify_recipients(self, token):
            return ["a@example.com"]

    class BoomNotifier:
        def notify_decided(self, **kwargs):
            raise RuntimeError("smtp down")

    token = "ap-" + "q" * 28
    with caplog.at_level(logging.WARNING, logger="atlas.api.main"):
        result = _apply_approval_decision(
            Broker(), {"graph_id": "g1", "node_id": "human-1"}, token,
            decision="approved", comment="同意", action_id=None, form=None,
            notifier=BoomNotifier(),
        )

    assert result["decision"] == "approved"  # 通知失败不改决策事实（既有语义）
    joined = "\n".join(record.getMessage() for record in caplog.records)
    assert "smtp down" in joined, "没有走到那条告警，断言就是空转"
    assert token not in joined
    assert token[:8] in joined
