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


def test_merged_alert_sends_lifecycle_notice():
    store, messages = _configured_store()
    _record_error_run(store)
    _record_error_run(store)
    alerts = store.list_alerts()
    assert any(a.count == 2 for a in alerts)
    assert len(messages.sent) == 2
    merged = messages.sent[1]
    assert merged["subject"] == "[Atlas告警][再次发生已归并] run_error"
    assert "累计：2 次" in merged["body"]
    assert "状态：open" in merged["body"]


def test_warning_min_severity_merged_also_notifies():
    store, messages = _configured_store(minSeverity="warning")
    _record_error_run(store)
    _record_error_run(store)
    subjects = sorted(x["subject"] for x in messages.sent)
    assert subjects == sorted([
        "[Atlas告警][critical] run_error",
        "[Atlas告警][warning] node_failed",
        "[Atlas告警][再次发生已归并] run_error",
        "[Atlas告警][再次发生已归并] node_failed",
    ])


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


def test_rollout_gate_merged_also_notifies():
    store, messages = _configured_store()
    store.raise_rollout_gate_alert(
        graph_id="g1", message="gate breached", action={"kind": "auto_rollback"}
    )
    second = store.raise_rollout_gate_alert(
        graph_id="g1", message="gate breached", action={"kind": "auto_rollback"}
    )
    assert second.count == 2
    assert len(messages.sent) == 2
    assert messages.sent[0]["subject"] == "[Atlas告警][critical] rollout_gate"
    assert messages.sent[1]["subject"] == "[Atlas告警][再次发生已归并] rollout_gate"


def test_without_notifier_record_run_has_zero_behavior():
    store = MonitoringStore()
    store.update_alert_channel(_raw())
    record = _record_error_run(store)
    assert record.status == "error"
    assert store.get_alert_channel_delivery() == AlertChannelDelivery()


def test_subject_falls_back_to_rule_id():
    alert = type("A", (), {"rule_id": "rollout_gate", "rule_name": "", "severity": "critical"})()
    assert build_alert_subject(alert) == "[Atlas告警][critical] rollout_gate"

def test_escalated_alert_sends_lifecycle_notice():
    store, messages = _configured_store()
    store.update_rules({
        "run_error": {"enabled": True},
        "node_failed": {"enabled": True},
        "consecutive_failures": {"enabled": True, "threshold": 3},
        "failure_rate": {"enabled": True, "window": 20, "min_samples": 5, "rate": 0.5},
        "custom": [],
        "escalation_ack_minutes": 1,
    })
    _record_error_run(store)
    # record_run 的 finished_at 取真实 now；先取回 node_failed，把 first_seen 拨早模拟未确认超时
    store.list_alerts()
    node_alert = next(a for a in store.list_alerts() if a.rule_id == "node_failed")
    node_alert.first_seen = "2026-09-20T00:00:00+00:00"
    alerts = store.list_alerts()  # 再次读触发升级
    upgraded = next(a for a in alerts if a.rule_id == "node_failed")
    assert upgraded.severity == "critical"
    assert upgraded.escalated_at
    assert messages.sent[-1]["subject"] == "[Atlas告警][未确认已升级] node_failed"
    assert "级别：critical" in messages.sent[-1]["body"]
def test_resolved_alert_sends_lifecycle_notice():
    store, messages = _configured_store()
    _record_error_run(store)
    alert = next(a for a in store.list_alerts() if a.rule_id == "run_error")
    resolved = store.resolve_alert(alert.id)
    assert resolved.status == "resolved"
    assert messages.sent[-1]["subject"] == "[Atlas告警][告警已解决] run_error"
    assert "状态：resolved" in messages.sent[-1]["body"]


def test_warning_lifecycle_filtered_by_min_severity():
    store, messages = _configured_store()  # critical only
    _record_error_run(store)
    alert = next(a for a in store.list_alerts() if a.rule_id == "node_failed")
    store.resolve_alert(alert.id)
    subjects = [x["subject"] for x in messages.sent]
    assert subjects == ["[Atlas告警][critical] run_error"]


def test_lifecycle_send_failure_is_fail_safe():
    class _Raising:
        @staticmethod
        def send(*a, **k):
            raise RuntimeError("network down")

    cfg = AlertChannel(enabled=True, to="https://robot.example.com")
    alert = type("A", (), {"severity": "critical"})()
    delivery = AlertNotifier(_Raising()).notify_lifecycle(
        alert, cfg, transition="resolved"
    )
    assert delivery.errorCode == "ALERT_NOTIFY_FAILED"



# --- docs/54 §6/§7：健康自动恢复（recovery）与 lifecycle 限流退避 ---
def _record_healthy_run(store: MonitoringStore, graph_id: str = "g1"):
    return store.record_run(
        graph_id=graph_id,
        mode="sync",
        status="completed",
        started_at="2026-09-23T02:00:00+00:00",
        duration_ms=10,
        nodes=[NodeResult(node_id="n1", node_type="tool_call", status="success")],
    )


class _StubAlert:
    id = "alt-1"
    rule_id = "run_error"
    rule_name = "run_error"
    graph_id = "g1"
    status = "open"
    severity = "critical"
    count = 1
    first_seen = "2026-09-23T00:00:00+00:00"
    last_seen = "2026-09-23T00:00:00+00:00"
    last_run_id = "run-1"
    assignee = None
    message = "boom"


def test_U531_healthy_run_auto_resolves_open_alert_and_notifies_recovery():
    store, messages = _configured_store()
    _record_error_run(store)
    _record_healthy_run(store)
    resolved = store.list_alerts(status="resolved")
    run_error = next(a for a in resolved if a.rule_id == "run_error")
    assert run_error.status == "resolved"
    subjects = [m["subject"] for m in messages.sent]
    assert "[Atlas告警][告警已自动恢复] run_error" in subjects


def test_U532_acknowledged_alert_is_not_auto_recovered():
    store, messages = _configured_store()
    _record_error_run(store)
    alert = next(a for a in store.list_alerts() if a.rule_id == "run_error")
    assert store.acknowledge_alert(alert.id).status == "acknowledged"
    _record_healthy_run(store)
    again = next(a for a in store.list_alerts() if a.rule_id == "run_error")
    assert again.status == "acknowledged"  # 确认过的不自动恢复
    assert not any("自动恢复" in m["subject"] for m in messages.sent)


def test_U533_rollout_gate_alert_is_not_auto_recovered():
    store, messages = _configured_store()
    store.raise_rollout_gate_alert(
        graph_id="g1", message="gate breached", action={"kind": "auto_rollback"}
    )
    _record_healthy_run(store)
    gate = next(a for a in store.list_alerts() if a.rule_id == "rollout_gate")
    assert gate.status == "open"  # 灰度门控告警不自动恢复
    assert not any("自动恢复" in m["subject"] for m in messages.sent)


def _throttled_notifier(messages, t):
    return AlertNotifier(
        messages,
        clock=lambda: "2026-09-23T01:00:00+00:00",
        lifecycle_min_interval_seconds=60,
        time_func=lambda: t["v"],
    )


def test_U534_lifecycle_throttled_within_interval_then_allowed():
    messages = FakeMessages()
    t = {"v": 0.0}
    notifier = _throttled_notifier(messages, t)
    cfg = AlertChannel(enabled=True, to="https://robot.example.com")
    alert = _StubAlert()

    first = notifier.notify_lifecycle(alert, cfg, transition="resolved")
    second = notifier.notify_lifecycle(alert, cfg, transition="resolved")
    assert first.lastNotifiedAt is not None
    assert second.lastNotifiedAt is None  # 60s 内限流跳过、不记投递
    assert len(messages.sent) == 1

    t["v"] = 61.0  # 超过间隔
    third = notifier.notify_lifecycle(alert, cfg, transition="resolved")
    assert third.lastNotifiedAt is not None
    assert len(messages.sent) == 2


def test_U535_throttle_is_per_transition_and_new_alert_unlimited():
    messages = FakeMessages()
    t = {"v": 0.0}
    notifier = _throttled_notifier(messages, t)
    cfg = AlertChannel(enabled=True, to="https://robot.example.com")
    alert = _StubAlert()

    notifier.notify_lifecycle(alert, cfg, transition="resolved")
    notifier.notify_lifecycle(alert, cfg, transition="recovery")  # 不同 transition 不限
    notifier.notify(alert, cfg)  # new 首条始终不限流
    notifier.notify_lifecycle(alert, cfg, transition="resolved")  # 仍在窗口内限流
    assert len(messages.sent) == 3


def test_U536_failed_lifecycle_send_is_also_throttled():
    class _Raising:
        @staticmethod
        def send(*a, **k):
            raise RuntimeError("network down")

    t = {"v": 0.0}
    notifier = AlertNotifier(
        _Raising(),
        lifecycle_min_interval_seconds=60,
        time_func=lambda: t["v"],
    )
    cfg = AlertChannel(enabled=True, to="https://robot.example.com")
    alert = _StubAlert()
    first = notifier.notify_lifecycle(alert, cfg, transition="resolved")
    assert first.errorCode == "ALERT_NOTIFY_FAILED"
    t["v"] = 10.0  # 仍在窗口内：失败也计时，不重试打爆渠道
    second = notifier.notify_lifecycle(alert, cfg, transition="resolved")
    assert second.lastNotifiedAt is None
    assert second.errorCode is None
