# -*- coding: utf-8 -*-
"""docs/56 §4：消息投递日志 ring + webhook/IM 网络类退避重试（进程内，零真实网络）。"""

from __future__ import annotations

import pytest

from atlas.message.service import MessageSendError, MessageService
from atlas.security.egress import EgressDenied


class _FlakyWebhook:
    """前 fail_times 次抛网络错，之后成功；记录调用次数。"""

    def __init__(self, fail_times: int = 0, exc: Exception | None = None) -> None:
        self.fail_times = fail_times
        self.exc = exc or RuntimeError("connection reset")
        self.calls = 0

    def send(self, url: str, payload: dict, secret: str | None = None) -> None:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.exc


class _AlwaysFailWebhook:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc
        self.calls = 0

    def send(self, url: str, payload: dict, secret: str | None = None) -> None:
        self.calls += 1
        raise self.exc


class _FlakyIm:
    def __init__(self, fail_times: int = 0) -> None:
        self.fail_times = fail_times
        self.calls = 0

    def send(self, channel: str, url: str, text: str, secret: str | None) -> None:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError("im 500")


class _FailEmail:
    def __init__(self) -> None:
        self.calls = 0

    def send(self, to, subject, body) -> None:
        self.calls += 1
        raise RuntimeError("smtp 550")


def _service(webhook=None, im=None, email=None):
    sleeps: list[float] = []
    svc = MessageService(
        email_sender=email,
        webhook_sender=webhook,
        im_sender=im,
        retry_delays=(0, 0),  # 零等待，仅验证退避次数
        sleep_func=sleeps.append,
    )
    return svc, sleeps


def test_demo_without_sender_logs_in_process():
    svc, _ = _service()
    svc.send("webhook", "https://x.example.com/hook", "s", "b")
    deliveries = svc.list_deliveries()
    assert len(deliveries) == 1
    assert deliveries[0]["status"] == "in_process"
    assert deliveries[0]["attempts"] == 1
    assert deliveries[0]["errorCode"] is None


def test_webhook_retries_then_succeeds_three_attempts():
    flaky = _FlakyWebhook(fail_times=2)
    svc, sleeps = _service(webhook=flaky)
    record = svc.send("webhook", "https://x.example.com/hook", "告警", "内容")
    assert record["delivered"] == "webhook"
    assert flaky.calls == 3
    assert sleeps == [0, 0]  # 两次退避
    delivery = svc.list_deliveries()[0]
    assert delivery["status"] == "delivered:webhook"
    assert delivery["attempts"] == 3
    assert delivery["errorCode"] is None


def test_webhook_persistent_failure_logs_failed_and_raises_without_message():
    bad = _AlwaysFailWebhook(RuntimeError("timeout"))
    svc, sleeps = _service(webhook=bad)
    before = svc.count
    with pytest.raises(MessageSendError) as exc:
        svc.send("webhook", "https://x.example.com/hook", "s", "b")
    assert exc.value.code == "WEBHOOK_SEND_FAILED"
    assert bad.calls == 3 and sleeps == [0, 0]
    assert svc.count == before  # 失败不写 _messages
    delivery = svc.list_deliveries()[0]
    assert delivery["status"] == "failed"
    assert delivery["attempts"] == 3
    assert delivery["errorCode"] == "WEBHOOK_SEND_FAILED"
    assert "timeout" in delivery["errorMessage"]


def test_egress_denied_not_retried():
    bad = _AlwaysFailWebhook(EgressDenied("EGRESS_DENIED", "blocked"))
    svc, sleeps = _service(webhook=bad)
    with pytest.raises(MessageSendError) as exc:
        svc.send("webhook", "http://169.254.169.254/latest", "s", "b")
    assert exc.value.code == "EGRESS_DENIED"
    assert bad.calls == 1 and sleeps == []  # 安全拦截立即失败
    assert svc.list_deliveries()[0]["status"] == "failed"
    assert svc.list_deliveries()[0]["attempts"] == 1


def test_email_smtp_failure_not_retried():
    mail = _FailEmail()
    svc, sleeps = _service(email=mail)
    with pytest.raises(MessageSendError) as exc:
        svc.send("email", "ops@example.com", "s", "b")
    assert exc.value.code == "SMTP_SEND_FAILED"
    assert mail.calls == 1 and sleeps == []
    assert svc.list_deliveries()[0]["errorCode"] == "SMTP_SEND_FAILED"


def test_im_network_failure_retried():
    im = _FlakyIm(fail_times=3)
    svc, _ = _service(im=im)
    with pytest.raises(MessageSendError) as exc:
        svc.send("dingtalk", "https://im.example.com/ding", "s", "b")
    assert exc.value.code == "IM_SEND_FAILED"
    assert im.calls == 3
    assert svc.list_deliveries()[0]["status"] == "failed"


def test_delivery_log_orders_limit_and_resets():
    svc, _ = _service()
    for i in range(3):
        svc.send("webhook", "https://x.example.com/h", f"告警-{i}", "b")
    deliveries = svc.list_deliveries()
    assert [d["subject"] for d in deliveries] == ["告警-2", "告警-1", "告警-0"]  # 倒序
    assert len(svc.list_deliveries(limit=1)) == 1
    assert len(svc.list_deliveries(limit=99999)) == 3  # clamp 到 ring 上限
    svc.reset()
    assert svc.list_deliveries() == []


def test_delivery_subject_truncated_to_100():
    svc, _ = _service()
    svc.send("webhook", "https://x.example.com/h", "告" * 150, "b")
    assert len(svc.list_deliveries()[0]["subject"]) == 100


def test_failed_delivery_error_message_truncated():
    bad = _AlwaysFailWebhook(RuntimeError("E" * 500))
    svc, _ = _service(webhook=bad)
    with pytest.raises(MessageSendError):
        svc.send("webhook", "https://x.example.com/h", "s", "b")
    assert len(svc.list_deliveries()[0]["errorMessage"]) == 300
