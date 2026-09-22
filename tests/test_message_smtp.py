# -*- coding: utf-8 -*-
"""SMTP 邮件真实投递测试（docs/34 §五 P0-2；D24 email 子集）。

不触网：SmtpSender 的连接工厂全部注入 fake；MessageService 用假 sender。
"""

from __future__ import annotations

import pytest

from atlas.harness.base import ActionRequest, ActionStatus, Permission
from atlas.message.adapter import MessageHarnessAdapter
from atlas.message.service import MessageSendError, MessageService
from atlas.message.smtp import SmtpConfig, SmtpError, SmtpSender


class FakeSmtp:
    """记录调用的假 SMTP 连接；可按用例预设 login/send 失败。"""

    instances: list["FakeSmtp"] = []

    def __init__(self, config, *, login_fail=False, send_fail=False):
        self.config = config
        self.login_fail = login_fail
        self.send_fail = send_fail
        self.logged_in: tuple[str, str] | None = None
        self.started_tls = False
        self.sent: list[object] = []
        self.quit_called = False
        FakeSmtp.instances.append(self)

    def starttls(self, context=None):
        self.started_tls = True

    def login(self, username, password):
        if self.login_fail:
            raise OSError("535 auth failed")
        self.logged_in = (username, password)

    def send_message(self, message):
        if self.send_fail:
            raise OSError("554 relay denied")
        self.sent.append(message)

    def quit(self):
        self.quit_called = True

    def close(self):
        pass


@pytest.fixture(autouse=True)
def _reset_fake_instances():
    FakeSmtp.instances.clear()
    yield
    FakeSmtp.instances.clear()


class RecordingSender:
    """MessageService 层的假 sender（只实现 send 协议）。"""

    def __init__(self, fail: bool = False):
        self.calls: list[tuple[list[str], str, str]] = []
        self.fail = fail

    def send(self, to, subject, body):
        if self.fail:
            raise SmtpError("boom")
        self.calls.append((list(to), subject, body))


# ---------- SmtpConfig.from_env ----------

def test_config_from_env_none_without_host():
    assert SmtpConfig.from_env({}) is None
    assert SmtpConfig.from_env({"ATLAS_SMTP_HOST": "  "}) is None


def test_config_from_env_full():
    cfg = SmtpConfig.from_env({
        "ATLAS_SMTP_HOST": "smtp.example.com",
        "ATLAS_SMTP_PORT": "2525",
        "ATLAS_SMTP_USERNAME": "apikey",
        "ATLAS_SMTP_PASSWORD": "secret",
        "ATLAS_SMTP_FROM": "noreply@example.com",
        "ATLAS_SMTP_USE_TLS": "false",
    })
    assert cfg is not None
    assert cfg.host == "smtp.example.com"
    assert cfg.port == 2525
    assert cfg.username == "apikey"
    assert cfg.password == "secret"
    assert cfg.from_addr == "noreply@example.com"
    assert cfg.use_tls is False


def test_config_from_env_defaults_port_and_tls():
    cfg = SmtpConfig.from_env({
        "ATLAS_SMTP_HOST": "smtp.example.com",
        "ATLAS_SMTP_USERNAME": "user@example.com",
    })
    assert cfg is not None
    assert cfg.port == 587
    assert cfg.use_tls is True
    assert cfg.from_addr == "user@example.com"  # 从用户名推导


def test_config_from_env_missing_from_fail_closed():
    with pytest.raises(SmtpError):
        SmtpConfig.from_env({"ATLAS_SMTP_HOST": "smtp.example.com", "ATLAS_SMTP_USERNAME": "apikey"})


def test_config_from_env_bad_port():
    with pytest.raises(SmtpError):
        SmtpConfig.from_env({"ATLAS_SMTP_HOST": "h", "ATLAS_SMTP_FROM": "a@b.c", "ATLAS_SMTP_PORT": "abc"})


# ---------- SmtpSender ----------

def _fake_factory(**fake_kw):
    """模拟 _default_factory 的完整建连契约（STARTTLS + login），再返回连接。"""
    def factory(cfg):
        smtp = FakeSmtp(cfg, **fake_kw)
        if cfg.use_tls:
            smtp.starttls()
        if cfg.username and cfg.password is not None:
            smtp.login(cfg.username, cfg.password)  # login_fail 时在此抛 OSError
        return smtp
    return factory


def _sender(**fake_kw):
    cfg = SmtpConfig(host="smtp.example.com", port=587, username="u",
                     password="p", from_addr="noreply@example.com", use_tls=True)
    return SmtpSender(cfg, smtp_factory=_fake_factory(**fake_kw))


def test_sender_send_builds_message_and_authenticates():
    sender = _sender()
    sender.send(["a@example.com", "b@example.com"], "主题", "正文")

    assert len(FakeSmtp.instances) == 1
    smtp = FakeSmtp.instances[0]
    assert smtp.started_tls is True
    assert smtp.logged_in == ("u", "p")
    assert smtp.quit_called is True
    assert len(smtp.sent) == 1
    msg = smtp.sent[0]
    assert msg["From"] == "noreply@example.com"
    assert msg["To"] == "a@example.com, b@example.com"
    assert msg["Subject"] == "主题"
    assert msg.get_content().strip() == "正文"


def test_sender_login_failure_raises_and_closes():
    sender = _sender(login_fail=True)
    with pytest.raises(SmtpError):
        sender.send(["a@example.com"], "s", "b")


def test_sender_send_failure_raises():
    sender = _sender(send_fail=True)
    with pytest.raises(SmtpError, match="554"):
        sender.send(["a@example.com"], "s", "b")


# ---------- MessageService 路由 ----------

def test_service_without_sender_keeps_in_process():
    service = MessageService()
    record = service.send("email", "ops@example.com", "标题", "正文")
    assert record["delivered"] == "in_process"
    assert service.count == 1


def test_service_with_sender_delivers_email():
    sender = RecordingSender()
    service = MessageService(email_sender=sender)
    record = service.send("email", ["ops@example.com"], "标题", "正文")

    assert record["delivered"] == "smtp"
    assert sender.calls == [(["ops@example.com"], "标题", "正文")]
    assert service.count == 1
    assert service.last_send["channel"] == "email"


def test_service_sender_not_used_for_non_email():
    sender = RecordingSender()
    service = MessageService(email_sender=sender)
    record = service.send("im", "user-1", "标题", "正文")
    assert record["delivered"] == "in_process"
    assert sender.calls == []


def test_service_sender_failure_raises_and_does_not_record():
    sender = RecordingSender(fail=True)
    service = MessageService(email_sender=sender)
    with pytest.raises(MessageSendError) as exc:
        service.send("email", "ops@example.com", "标题", "正文")
    assert exc.value.code == "SMTP_SEND_FAILED"
    assert service.count == 0  # 失败不落记录


# ---------- 适配器层错误码 ----------

def _adapter(service):
    return MessageHarnessAdapter(
        service=service,
        granted_permissions={Permission.READ, Permission.WRITE, Permission.DELETE, Permission.FINANCIAL},
    )


def test_adapter_email_send_success():
    adapter = _adapter(MessageService(email_sender=RecordingSender()))
    result = adapter._execute(ActionRequest(
        capability_name="send",
        parameters={"channel": "email", "to": "ops@example.com", "subject": "s", "body": "b"},
    ))
    assert result.status is ActionStatus.SUCCESS
    assert result.output["delivered"] == "smtp"


def test_adapter_email_send_failure_maps_error_code():
    adapter = _adapter(MessageService(email_sender=RecordingSender(fail=True)))
    result = adapter._execute(ActionRequest(
        capability_name="send",
        parameters={"channel": "email", "to": "ops@example.com", "subject": "s", "body": "b"},
    ))
    assert result.status is ActionStatus.FAILED
    assert result.error.code == "SMTP_SEND_FAILED"
