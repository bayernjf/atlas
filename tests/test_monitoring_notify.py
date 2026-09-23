import pytest

from atlas.monitoring.notify import (
    AlertChannel,
    AlertChannelDelivery,
    validate_alert_channel,
)
from atlas.monitoring.records import MonitoringStore


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
