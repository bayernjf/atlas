import pytest

from atlas.message.service import MessageSendError
from atlas.monitoring.notify import (
    AlertChannel,
    AlertChannelDelivery,
    AlertNotifier,
    build_alert_body,
    build_alert_subject,
    validate_alert_channel,
)
from atlas.monitoring.records import MonitoringStore
from atlas.monitoring.metrics import NodeResult


def _raw(**overrides):
    raw = {
        "enabled": True,
        "channel": "dingtalk",
        "to": "https://robot.example.com/send",
        "secret": "",
        "minSeverity": "critical",
    }
    raw.update(overrides)
    return raw


def test_default_channel_is_disabled_and_empty():
    channel = MonitoringStore().get_alert_channel()
    assert channel.enabled is False
    assert channel.to == ""
    assert channel.updatedAt == ""


def test_valid_url_channel_updates_with_timestamp():
    store = MonitoringStore()
    channel = store.update_alert_channel(_raw())
    assert channel.enabled is True
    assert channel.to.startswith("https://")
    assert channel.updatedAt


def test_enabled_requires_target():
    errors = validate_alert_channel(_raw(to="   "))
    assert "通知目标" in "；".join(errors)


def test_url_channel_rejects_non_url():
    errors = validate_alert_channel(_raw(to="robot.example.com/send"))
    assert any("URL" in e for e in errors)


def test_email_channel_requires_at_sign():
    errors = validate_alert_channel(
        _raw(channel="email", to="ops-example.com")
    )
    assert any("邮箱" in e for e in errors)


def test_email_channel_accepts_address():
    channel = MonitoringStore().update_alert_channel(
        _raw(channel="email", to="ops@example.com")
    )
    assert channel.channel == "email"


def test_secret_only_for_dingtalk_and_feishu():
    errors = validate_alert_channel(
        _raw(channel="wecom", secret="SEC")
    )
    assert any("加签密钥" in e for e in errors)
    assert validate_alert_channel(
        _raw(channel="feishu", secret="SEC")
    ) == []


def test_secret_max_length():
    errors = validate_alert_channel(_raw(secret="x" * 201))
    assert any("200" in e for e in errors)


def test_invalid_channel_and_min_severity():
    errors = validate_alert_channel(_raw(channel="sms", minSeverity="info"))
    joined = "；".join(errors)
    assert "通知渠道" in joined
    assert "最低告警级别" in joined


def test_disabled_channel_skips_content_validation():
    assert validate_alert_channel(
        _raw(enabled=False, to="", secret="SEC")
    ) == []


def test_update_invalid_raises_value_error():
    with pytest.raises(ValueError):
        MonitoringStore().update_alert_channel(_raw(to="not-a-url"))


def test_delivery_record_and_read():
    store = MonitoringStore()
    store.record_alert_channel_delivery(
        AlertChannelDelivery(
            lastNotifiedAt="2026-09-23T00:00:00+00:00",
            errorCode="EGRESS_DENIED",
            errorMessage="blocked",
        )
    )
    delivery = store.get_alert_channel_delivery()
    assert delivery.errorCode == "EGRESS_DENIED"
    assert delivery.lastNotifiedAt


def test_reset_clears_channel_and_delivery():
    store = MonitoringStore()
    store.update_alert_channel(_raw())
    store.record_alert_channel_delivery(
        AlertChannelDelivery(errorCode="IM_SEND_FAILED")
    )
    store.reset()
    assert store.get_alert_channel() == AlertChannel()
    assert store.get_alert_channel_delivery() == AlertChannelDelivery()


class FakeMessages:
    def __init__(self, *, fail: tuple[str, str] | None = None):
        self.sent: list[dict] = []
        self._fail = fail

    def send(self, channel, to, subject, body, secret=None):
        if self._fail is not None:
            raise MessageSendError(self._fail[0], self._fail[1])
        self.sent.append(
            {
                "channel": channel,
                "to": to,
                "subject": subject,
                "body": body,
                "secret": secret,
            }
        )


def _record_error_run(store: MonitoringStore, graph_id: str = "g1"):
    return store.record_run(
        graph_id=graph_id,
        mode="sync",
        status="error",
        started_at="2026-09-23T00:00:00+00:00",
        duration_ms=42,
        nodes=[NodeResult(node_id="n1", node_type="tool_call", status="failed", error="boom")],
        error="run failed",
    )


def _configured_store(**overrides):
    store = MonitoringStore()
    raw = _raw(secret="SEC123", **overrides)
    store.update_alert_channel(raw)
    messages = FakeMessages()
    store.set_notifier(AlertNotifier(messages, clock=lambda: "2026-09-23T01:00:00+00:00"))
    return store, messages


def test_new_critical_alert_sends_fixed_text_with_secret():
    store, messages = _configured_store()
    _record_error_run(store)
    assert len(messages.sent) == 1
    sent = messages.sent[0]
    assert sent["channel"] == "dingtalk"
    assert sent["to"] == "https://robot.example.com/send"
    assert sent["secret"] == "SEC123"
    assert sent["subject"] == "[Atlas告警][critical] run_error"
    body = sent["body"]
    assert "图：g1" in body
    assert "级别：critical" in body
    assert "值班：未指派" in body
    assert "首次：" in body


def test_merged_alert_does_not_notify_again():
    store, messages = _configured_store()
    _record_error_run(store)
    _record_error_run(store)
    alerts = store.list_alerts()
    assert any(a.count == 2 for a in alerts)
    assert len(messages.sent) == 1


def test_warning_min_severity_notifies_warning_alert_once():
    store, messages = _configured_store(minSeverity="warning")
    _record_error_run(store)
    _record_error_run(store)
    subjects = sorted(s["subject"] for s in messages.sent)
    assert subjects == ["[Atlas告警][critical] run_error", "[Atlas告警][warning] node_failed"]
    assert "级别：warning" in next(s["body"] for s in messages.sent if "warning" in s["subject"])


def test_disabled_channel_sends_nothing():
    store, messages = _configured_store(enabled=False, to="")
    _record_error_run(store)
    assert messages.sent == []
    assert store.get_alert_channel_delivery() == AlertChannelDelivery()


def test_send_failure_is_fail_safe_and_recorded():
    store = MonitoringStore()
    store.update_alert_channel(_raw())
    messages = FakeMessages(fail=("IM_SEND_FAILED", "robot rejected"))
    store.set_notifier(AlertNotifier(messages, clock=lambda: "2026-09-23T01:00:00+00:00"))
    record = _record_error_run(store)
    assert record.status == "error"
    delivery = store.get_alert_channel_delivery()
    assert delivery.errorCode == "IM_SEND_FAILED"
    assert delivery.errorMessage == "robot rejected"
    assert delivery.lastNotifiedAt == "2026-09-23T01:00:00+00:00"


def test_generic_send_failure_gets_fallback_error_code():
    class _Raising:
        @staticmethod
        def send(*a, **k):
            raise RuntimeError("network down")

    cfg = AlertChannel(enabled=True, to="https://robot.example.com")
    alert = type("A", (), {"severity": "critical"})()
    delivery = AlertNotifier(_Raising()).notify(alert, cfg)
    assert delivery.errorCode == "ALERT_NOTIFY_FAILED"


def test_rollout_gate_alert_notifies_only_when_new():
    store, messages = _configured_store()
    first = store.raise_rollout_gate_alert(
        graph_id="g1", message="gate breached", action={"kind": "auto_rollback"}
    )
    assert first.severity == "critical"
    second = store.raise_rollout_gate_alert(
        graph_id="g1", message="gate breached", action={"kind": "auto_rollback"}
    )
    assert second.count == 2
    assert len(messages.sent) == 1
    assert messages.sent[0]["subject"] == "[Atlas告警][critical] rollout_gate"


def test_without_notifier_record_run_has_zero_behavior():
    store = MonitoringStore()
    store.update_alert_channel(_raw())
    record = _record_error_run(store)
    assert record.status == "error"
    assert store.get_alert_channel_delivery() == AlertChannelDelivery()


def test_subject_falls_back_to_rule_id():
    alert = type("A", (), {"rule_id": "rollout_gate", "rule_name": "", "severity": "critical"})()
    assert build_alert_subject(alert) == "[Atlas告警][critical] rollout_gate"
