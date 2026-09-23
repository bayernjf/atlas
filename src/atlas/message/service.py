"""进程内消息服务（04 §4.8 权威契约 / 06 §6.7 运行时）。

默认只记录、不真实投递：send 返回消息记录并保存在进程内列表，重启清空、
reset 清空，GET /api/demo/messages 陪同查看。channel=email 且注入了
SmtpSender（ATLAS_SMTP_HOST 已配置）时真实发信，记录标 delivered="smtp"；
channel=webhook 且注入 WebhookSender 时向单个 URL POST JSON、标 delivered="webhook"
（docs/35 §3 T3，先过 SSRF 出向校验）；channel=dingtalk/wecom/feishu 且注入
ImSender 时投递群机器人（docs/51，标渠道名）；短信渠道仍缓做 docs/14 D24。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from atlas.security.egress import EgressDenied
from .im import IM_CHANNELS

MAX_RECIPIENTS = 20
MAX_SECRET_LENGTH = 200


class MessageSendError(Exception):
    """参数校验失败，code 对应 StructuredError.code。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _require_non_empty(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MessageSendError("MISSING_PARAMETER", f"缺少 {field}")
    return value


def _normalize_recipients(to: object) -> list[str]:
    if isinstance(to, str):
        recipients = [to]
    elif isinstance(to, list) and all(isinstance(item, str) for item in to):
        recipients = to
    else:
        raise MessageSendError("INVALID_PARAMETER", "to 必须是字符串或字符串数组")
    recipients = [item.strip() for item in recipients if item.strip()]
    if not recipients:
        raise MessageSendError("MISSING_PARAMETER", "缺少 to")
    if len(recipients) > MAX_RECIPIENTS:
        raise MessageSendError("INVALID_PARAMETER", f"群发收件人上限 {MAX_RECIPIENTS} 个")
    return recipients


class MessageService:
    def __init__(
        self,
        email_sender: object | None = None,
        webhook_sender: object | None = None,
        im_sender: object | None = None,
    ) -> None:
        # email_sender 需实现 send(to: list[str], subject: str, body: str)，
        # 生产为 message.smtp.SmtpSender；None 时 email 也只记录不投递（demo）。
        self._email_sender = email_sender
        # webhook_sender 需实现 send(url: str, payload: dict)，生产为 message.webhook.DefaultWebhookSender；
        # None 时 webhook 仅进程内记录（demo/测试）。
        self._webhook_sender = webhook_sender
        # im_sender 需实现 send(channel: str, url: str, text: str, secret: str | None)，
        # 生产为 message.im.DefaultImSender；None 时 IM 三渠道仅进程内记录（demo/测试）。
        self._im_sender = im_sender
        self._messages: list[dict[str, object]] = []
        self.last_send: dict[str, object] | None = None

    def send(
        self,
        channel: object,
        to: object,
        subject: object,
        body: object,
        secret: object = None,
    ) -> dict[str, object]:
        channel_value = _require_non_empty(channel, "channel").strip()
        subject_value = _require_non_empty(subject, "subject")
        body_value = _require_non_empty(body, "body")
        secret_value = self._validate_secret(channel_value, secret)
        # webhook 与 IM 渠道只接受单个 URL 字符串（数组即使单元素也 422，docs/35 §3、docs/51）。
        if channel_value in ("webhook", *IM_CHANNELS) and isinstance(to, list):
            raise MessageSendError(
                "INVALID_PARAMETER", f"{channel_value} 渠道的 to 必须是单个 URL 字符串"
            )
        recipients = _normalize_recipients(to)
        if channel_value == "email" and any("@" not in address for address in recipients):
            raise MessageSendError("INVALID_PARAMETER", "email 渠道的收件地址必须包含 @")

        record: dict[str, object] = {
            "id": str(uuid.uuid4()),
            "channel": channel_value,
            "to": recipients,
            "subject": subject_value,
            "body": body_value,
            "sent_at": datetime.now(timezone.utc).isoformat(),
            "delivered": "in_process",
        }
        if channel_value == "email" and self._email_sender is not None:
            try:
                self._email_sender.send(recipients, subject_value, body_value)
            except Exception as exc:
                # 投递失败不写记录（非幂等写能力，失败须显式），折算统一错误码
                raise MessageSendError("SMTP_SEND_FAILED", f"邮件投递失败：{exc}") from exc
            record["delivered"] = "smtp"
        elif channel_value == "webhook" and self._webhook_sender is not None:
            url = recipients[0]
            payload = {
                "id": record["id"],
                "channel": "webhook",
                "subject": subject_value,
                "body": body_value,
                "sent_at": record["sent_at"],
            }
            try:
                self._webhook_sender.send(url, payload)
            except EgressDenied as exc:
                # SSRF/非法 URL：透传安全错误码（EGRESS_DENIED/EGRESS_INVALID_URL），不写记录
                raise MessageSendError(exc.code, f"webhook 出向被拦截：{exc}") from exc
            except Exception as exc:
                # 网络/超时/非 2xx：统一 WEBHOOK_SEND_FAILED，不写记录
                raise MessageSendError("WEBHOOK_SEND_FAILED", f"webhook 投递失败：{exc}") from exc
            record["delivered"] = "webhook"
        elif channel_value in IM_CHANNELS and self._im_sender is not None:
            text = f"{subject_value}\n{body_value}"
            try:
                self._im_sender.send(channel_value, recipients[0], text, secret_value)
            except EgressDenied as exc:
                raise MessageSendError(exc.code, f"{channel_value} 出向被拦截：{exc}") from exc
            except Exception as exc:
                raise MessageSendError("IM_SEND_FAILED", f"IM 投递失败：{exc}") from exc
            record["delivered"] = channel_value
        self._messages.append(record)
        self.last_send = {k: record[k] for k in ("id", "channel", "to", "sent_at")}
        return record

    @staticmethod
    def _validate_secret(channel: str, secret: object) -> str | None:
        # secret 仅 dingtalk/feishu 允许：防止在不支持的渠道误以为消息已加签。
        if secret is None or (isinstance(secret, str) and not secret.strip()):
            return None
        if not isinstance(secret, str):
            raise MessageSendError("INVALID_PARAMETER", "secret 必须是字符串")
        value = secret.strip()
        if channel not in ("dingtalk", "feishu"):
            raise MessageSendError(
                "INVALID_PARAMETER", f"{channel} 渠道不支持 secret（仅 dingtalk/feishu）"
            )
        if len(value) > MAX_SECRET_LENGTH:
            raise MessageSendError(
                "INVALID_PARAMETER", f"secret 长度上限 {MAX_SECRET_LENGTH} 字符"
            )
        return value

    def list(self) -> list[dict[str, object]]:
        return list(self._messages)

    def reset(self) -> None:
        self._messages = []
        self.last_send = None

    @property
    def count(self) -> int:
        return len(self._messages)
