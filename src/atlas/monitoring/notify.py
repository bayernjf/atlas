from typing import Literal

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
