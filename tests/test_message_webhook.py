# -*- coding: utf-8 -*-
"""T3 webhook 渠道测试（docs/35 §3，D24 webhook 子集）。

DefaultWebhookSender：2xx 成功、非 2xx/网络错 → WebhookDeliveryError、EgressDenied 透传；
MessageService：webhook 单个 URL、数组 422、payload 形状、EGRESS_* 透传/WEBHOOK_SEND_FAILED、
失败不写记录、未装配回退 in_process；email 回归不受影响。全程 fake 不触网。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from atlas.message.service import MessageSendError, MessageService
from atlas.message.webhook import (
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    WEBHOOK_TIMEOUT_SECONDS,
    DefaultWebhookSender,
    WebhookDeliveryError,
    serialize_payload,
    sign_body,
)
from atlas.security.egress import EgressDenied


class FakeGuard:
    def __init__(self, deny_code: str | None = None) -> None:
        self.deny_code = deny_code
        self.checked: list[str] = []

    def check(self, url: str):
        self.checked.append(url)
        if self.deny_code:
            raise EgressDenied(self.deny_code, f"blocked: {url}")
        return SimpleNamespace(host=url)


class FakePost:
    def __init__(self, status: int = 200, exc: Exception | None = None) -> None:
        self.status = status
        self.exc = exc
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        if self.exc is not None:
            raise self.exc
        return SimpleNamespace(status_code=self.status)


# ============================ DefaultWebhookSender ============================


def test_webhook_sender_success_posts_deterministic_json_bytes():
    post = FakePost(status=200)
    sender = DefaultWebhookSender(guard=FakeGuard(), post=post)
    payload = {"id": "x", "channel": "webhook", "subject": "告警", "body": "b", "sent_at": "t"}
    sender.send("https://hooks.example.com/in", payload)
    assert len(post.calls) == 1
    url, kwargs = post.calls[0]
    assert url == "https://hooks.example.com/in"
    # docs/58：确定性紧凑 UTF-8 字节（content=），不再用 httpx json= 隐式序列化
    assert kwargs["content"] == serialize_payload(payload)
    assert json.loads(kwargs["content"].decode("utf-8")) == payload
    assert "json" not in kwargs
    assert kwargs["headers"]["Content-Type"] == "application/json; charset=utf-8"
    assert kwargs["timeout"] == WEBHOOK_TIMEOUT_SECONDS == 10.0
    # 无 secret：不发签名头
    assert TIMESTAMP_HEADER not in kwargs["headers"]
    assert SIGNATURE_HEADER not in kwargs["headers"]


def test_webhook_sender_hmac_signature_fixed_vector():
    """docs/58 §2：固定时钟/密钥/载荷的逐字节签名向量（离线算准后固化）。"""
    post = FakePost(status=200)
    clock = iter([1700000000.0])
    sender = DefaultWebhookSender(guard=FakeGuard(), post=post, clock=lambda: next(clock))
    payload = {
        "id": "m1",
        "channel": "webhook",
        "subject": "告警",
        "body": "**失败率** 0.2",
        "sent_at": "2026-09-24T00:00:00+00:00",
    }
    sender.send("https://hooks.example.com/in", payload, secret="atlas-test-secret")
    _, kwargs = post.calls[0]
    raw = kwargs["content"]
    headers = kwargs["headers"]
    assert headers[TIMESTAMP_HEADER] == "1700000000"
    expected_hex = "49b92da7b712ff7570b9613bee200c7cbfbe1009856eb5fe790e5b16d4041f83"
    assert headers[SIGNATURE_HEADER] == f"sha256={expected_hex}"
    # 签名覆盖字节 == 实际发送字节（接收方可对 raw 重算）
    assert sign_body("atlas-test-secret", "1700000000", raw) == expected_hex
    # 紧凑序列化：无多余空格、中文不转义
    assert raw == serialize_payload(payload)
    assert "失败率" in raw.decode("utf-8")


def test_webhook_sender_signature_covers_exact_sent_bytes():
    """篡改发送字节后签名不匹配（签名与 content 绑定）。"""
    post = FakePost(status=200)
    sender = DefaultWebhookSender(
        guard=FakeGuard(), post=post, clock=lambda: 1700000000.0
    )
    sender.send("https://hooks.example.com/in", {"a": 1}, secret="k")
    _, kwargs = post.calls[0]
    raw = kwargs["content"]
    sig = kwargs["headers"][SIGNATURE_HEADER]
    tampered = raw + b" "
    assert sign_body("k", "1700000000", tampered) != sig.removeprefix("sha256=")


@pytest.mark.parametrize("status", [201, 204, 301, 400, 404, 500, 503])
def test_webhook_sender_non_2xx_raises(status):
    sender = DefaultWebhookSender(guard=FakeGuard(), post=FakePost(status=status))
    if 200 <= status < 300:
        sender.send("https://hooks.example.com/in", {})
    else:
        with pytest.raises(WebhookDeliveryError):
            sender.send("https://hooks.example.com/in", {})


@pytest.mark.parametrize(
    "exc",
    [httpx.ConnectError("boom"), httpx.TimeoutException("slow"), httpx.ReadError("reset")],
)
def test_webhook_sender_network_error_raises(exc):
    sender = DefaultWebhookSender(guard=FakeGuard(), post=FakePost(exc=exc))
    with pytest.raises(WebhookDeliveryError):
        sender.send("https://hooks.example.com/in", {})


def test_webhook_sender_egress_denied_passthrough():
    sender = DefaultWebhookSender(guard=FakeGuard("EGRESS_DENIED"), post=FakePost())
    with pytest.raises(EgressDenied) as ei:
        sender.send("https://internal.example.corp/x", {})
    assert ei.value.code == "EGRESS_DENIED"


def test_webhook_sender_egress_invalid_url_passthrough():
    sender = DefaultWebhookSender(guard=FakeGuard("EGRESS_INVALID_URL"), post=FakePost())
    with pytest.raises(EgressDenied) as ei:
        sender.send("http://127.0.0.1:8000/hook", {})
    assert ei.value.code == "EGRESS_INVALID_URL"


def test_webhook_sender_checks_before_post():
    # 被 egress 拦截时绝不发起 POST
    post = FakePost()
    sender = DefaultWebhookSender(guard=FakeGuard("EGRESS_DENIED"), post=post)
    with pytest.raises(EgressDenied):
        sender.send("http://169.254.169.254/latest/meta-data", {})
    assert post.calls == []


# ============================ MessageService ============================


class RecordingWebhookSender:
    def __init__(self, exc: Exception | None = None) -> None:
        self.exc = exc
        self.calls: list[tuple[str, dict, str | None]] = []

    def send(self, url: str, payload: dict, secret: str | None = None) -> None:
        self.calls.append((url, payload, secret))
        if self.exc is not None:
            raise self.exc


def test_service_webhook_success_record_and_payload():
    hook = RecordingWebhookSender()
    svc = MessageService(webhook_sender=hook)
    rec = svc.send("webhook", "https://hooks.example.com/in", "审批挂起", "有一笔退款待审批")
    assert rec["delivered"] == "webhook"
    assert rec["to"] == ["https://hooks.example.com/in"]
    assert svc.count == 1
    assert len(hook.calls) == 1
    url, payload, secret = hook.calls[0]
    assert url == "https://hooks.example.com/in"
    assert secret is None
    assert payload["channel"] == "webhook"
    assert payload["id"] == rec["id"]
    assert payload["subject"] == "审批挂起"
    assert payload["body"] == "有一笔退款待审批"
    assert payload["sent_at"] == rec["sent_at"]
    assert set(payload.keys()) == {"id", "channel", "subject", "body", "sent_at"}


def test_service_webhook_secret_accepted_and_passed_through():
    """docs/58：webhook 渠道开放 secret（HMAC 签名密钥），原样透传给 sender。"""
    hook = RecordingWebhookSender()
    svc = MessageService(webhook_sender=hook)
    rec = svc.send(
        "webhook", "https://hooks.example.com/in", "告警", "正文", secret="  hmac-key  "
    )
    assert rec["delivered"] == "webhook"
    assert hook.calls[0][2] == "hmac-key"  # strip 后透传


def test_service_webhook_blank_secret_passes_none():
    hook = RecordingWebhookSender()
    svc = MessageService(webhook_sender=hook)
    svc.send("webhook", "https://hooks.example.com/in", "s", "b", secret="   ")
    assert hook.calls[0][2] is None


def test_service_wecom_secret_still_rejected():
    """wecom 机器人 webhook URL 自带 key，传 secret 仍 INVALID_PARAMETER。"""
    svc = MessageService()
    with pytest.raises(MessageSendError) as ei:
        svc.send("wecom", "https://qyapi.weixin.qq.com/x", "s", "b", secret="k")
    assert ei.value.code == "INVALID_PARAMETER"


def test_service_webhook_list_to_rejected():
    hook = RecordingWebhookSender()
    svc = MessageService(webhook_sender=hook)
    # 数组即使单元素也 422
    with pytest.raises(MessageSendError) as ei:
        svc.send("webhook", ["https://hooks.example.com/in"], "s", "b")
    assert ei.value.code == "INVALID_PARAMETER"
    assert hook.calls == []
    assert svc.count == 0  # 失败不写记录


def test_service_webhook_egress_denied_passthrough_no_record():
    hook = RecordingWebhookSender(exc=EgressDenied("EGRESS_DENIED", "blocked"))
    svc = MessageService(webhook_sender=hook)
    with pytest.raises(MessageSendError) as ei:
        svc.send("webhook", "https://intranet.local/hook", "s", "b")
    assert ei.value.code == "EGRESS_DENIED"
    assert svc.count == 0


def test_service_webhook_invalid_url_passthrough_no_record():
    hook = RecordingWebhookSender(exc=EgressDenied("EGRESS_INVALID_URL", "bad"))
    svc = MessageService(webhook_sender=hook)
    with pytest.raises(MessageSendError) as ei:
        svc.send("webhook", "ftp://nope", "s", "b")
    assert ei.value.code == "EGRESS_INVALID_URL"
    assert svc.count == 0


def test_service_webhook_delivery_failure_code_no_record():
    hook = RecordingWebhookSender(exc=WebhookDeliveryError("500"))
    svc = MessageService(webhook_sender=hook)
    with pytest.raises(MessageSendError) as ei:
        svc.send("webhook", "https://hooks.example.com/in", "s", "b")
    assert ei.value.code == "WEBHOOK_SEND_FAILED"
    assert svc.count == 0


def test_service_webhook_without_sender_falls_back_in_process():
    svc = MessageService()  # 不装配 webhook sender
    rec = svc.send("webhook", "https://hooks.example.com/in", "s", "b")
    assert rec["delivered"] == "in_process"
    assert svc.count == 1


def test_service_email_still_works_alongside_webhook():
    class FakeSmtp:
        def __init__(self):
            self.sent = []

        def send(self, recipients, subject, body):
            self.sent.append((recipients, subject, body))

    smtp = FakeSmtp()
    hook = RecordingWebhookSender()
    svc = MessageService(email_sender=smtp, webhook_sender=hook)
    mail = svc.send("email", "ops@example.com", "主题", "正文")
    wh = svc.send("webhook", "https://hooks.example.com/in", "s", "b")
    assert mail["delivered"] == "smtp"
    assert wh["delivered"] == "webhook"
    assert len(smtp.sent) == 1 and smtp.sent[0][0] == ["ops@example.com"]
    assert len(hook.calls) == 1
    assert svc.count == 2
