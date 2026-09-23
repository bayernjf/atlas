from datetime import datetime, timezone
from typing import Callable, Literal

from pydantic import BaseModel

AlertChannelKind = Literal["dingtalk", "wecom", "feishu", "webhook", "email"]

ALERT_CHANNELS = ("dingtalk", "wecom", "feishu", "webhook", "email")
ALERT_SECRET_CHANNELS = ("dingtalk", "feishu")
ALERT_MIN_SEVERITIES = ("critical", "warning")
URL_CHANNELS = ("dingtalk", "wecom", "feishu", "webhook")


class AlertChannel(BaseModel):
    enabled: bool = False
    channel: AlertChannelKind = "dingtalk"
    to: str = ""
    secret: str = ""
    minSeverity: Literal["critical", "warning"] = "critical"
    updatedAt: str = ""


class AlertChannelDelivery(BaseModel):
    lastNotifiedAt: str | None = None
    errorCode: str | None = None
    errorMessage: str | None = None


def _is_http_url(value: str) -> bool:
    head = value.split("://", 1)
    if len(head) != 2 or head[0] not in ("http", "https") or not head[1]:
        return False
    return True


def validate_alert_channel(raw: dict) -> list[str]:
    errors: list[str] = []
    enabled = raw.get("enabled")
    if not isinstance(enabled, bool):
        errors.append("启用状态必须为布尔值")
    channel = raw.get("channel")
    if channel not in ALERT_CHANNELS:
        errors.append("通知渠道必须是 dingtalk/wecom/feishu/webhook/email 之一")
    to = raw.get("to")
    if not isinstance(to, str):
        errors.append("通知目标必须为字符串")
    secret = raw.get("secret")
    if not isinstance(secret, str):
        errors.append("加签密钥必须为字符串")
    min_severity = raw.get("minSeverity")
    if min_severity not in ALERT_MIN_SEVERITIES:
        errors.append("最低告警级别必须是 critical 或 warning")
    if isinstance(secret, str) and len(secret) > 200:
        errors.append("加签密钥长度不能超过 200")
    if isinstance(channel, str) and isinstance(secret, str) and secret:
        if channel not in ALERT_SECRET_CHANNELS:
            errors.append("仅钉钉和飞书渠道支持加签密钥")
    if enabled:
        if isinstance(to, str) and not to.strip():
            errors.append("启用通知时必须填写通知目标")
        elif isinstance(to, str) and to.strip():
            target = to.strip()
            if channel in URL_CHANNELS:
                if not _is_http_url(target):
                    errors.append("通知目标必须是 http(s) 开头的完整 URL")
            elif channel == "email" and "@" not in target:
                errors.append("通知目标必须是有效的邮箱地址")
    return errors


def alert_channel_from_raw(raw: dict) -> AlertChannel:
    return AlertChannel(
        enabled=bool(raw.get("enabled", False)),
        channel=raw.get("channel", "dingtalk"),
        to=str(raw.get("to", "")).strip(),
        secret=str(raw.get("secret", "")),
        minSeverity=raw.get("minSeverity", "critical"),
        updatedAt=str(raw.get("updatedAt", "")),
    )


def alert_rule_label(alert: object) -> str:
    rule_name = getattr(alert, "rule_name", None)
    if isinstance(rule_name, str) and rule_name.strip():
        return rule_name.strip()
    return str(getattr(alert, "rule_id"))


def build_alert_subject(alert: object) -> str:
    severity = str(getattr(alert, "severity"))
    return f"[Atlas告警][{severity}] {alert_rule_label(alert)}"


def build_alert_body(alert: object) -> str:
    assignee = getattr(alert, "assignee", None)
    assignee_text = assignee if isinstance(assignee, str) and assignee.strip() else "未指派"
    lines = [
        str(getattr(alert, "message")),
        f"图：{getattr(alert, 'graph_id')}",
        f"运行：{getattr(alert, 'last_run_id') or ''}",
        f"级别：{getattr(alert, 'severity')}",
        f"值班：{assignee_text}",
        f"首次：{getattr(alert, 'first_seen')}",
    ]
    return "\n".join(lines)


class AlertNotifier:
    def __init__(
        self,
        message_service: object,
        *,
        clock: Callable[[], str] | None = None,
    ) -> None:
        self._messages = message_service
        self._clock = clock or (
            lambda: datetime.now(timezone.utc).isoformat()
        )

    def should_notify(self, cfg: AlertChannel, alert: object) -> bool:
        if not cfg.enabled or not cfg.to.strip():
            return False
        if cfg.minSeverity == "critical":
            return str(getattr(alert, "severity")) == "critical"
        return True

    def notify(self, alert: object, cfg: AlertChannel) -> AlertChannelDelivery:
        if not self.should_notify(cfg, alert):
            return AlertChannelDelivery()
        try:
            self._messages.send(
                cfg.channel,
                cfg.to,
                build_alert_subject(alert),
                build_alert_body(alert),
                secret=cfg.secret or None,
            )
        except Exception as exc:
            code = getattr(exc, "code", None)
            return AlertChannelDelivery(
                lastNotifiedAt=self._clock(),
                errorCode=code if isinstance(code, str) else "ALERT_NOTIFY_FAILED",
                errorMessage=str(exc)[:300],
            )
        return AlertChannelDelivery(lastNotifiedAt=self._clock())

