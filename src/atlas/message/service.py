"""进程内消息服务（04 §4.8 权威契约 / 06 §6.7 运行时）。

默认只记录、不真实投递：send 返回消息记录并保存在进程内列表，重启清空、
reset 清空，GET /api/demo/messages 陪同查看。channel=email 且注入了
SmtpSender（ATLAS_SMTP_HOST 已配置）时真实发信，记录标 delivered="smtp"；
channel=webhook 且注入 WebhookSender 时向 1-20 个 URL POST JSON（docs/58 群发、
HMAC 签名）、标 delivered="webhook"（docs/35 §3 T3，先过 SSRF 出向校验）；
channel=dingtalk/wecom/feishu 且注入 ImSender 时投递群机器人（docs/51，docs/58
markdown 富文本/@人/多 URL，标渠道名）；短信渠道仍缓做 docs/14 D24。
"""

from __future__ import annotations

import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Callable

from atlas.security.egress import EgressDenied
from .im import IM_CHANNELS, normalize_mentions

MAX_RECIPIENTS = 20
MAX_SECRET_LENGTH = 200

# docs/56 §4：投递日志 ring 与退避重试参数
DELIVERY_RING_SIZE = 200
DELIVERY_LIST_LIMIT = 100
SUBJECT_LOG_LIMIT = 100
ERROR_LOG_LIMIT = 300
# 仅 webhook / IM 的网络类错误重试（共 3 次尝试：初次 + 2 次退避）；
# EGRESS_*（SSRF/非法 URL）、参数类、SMTP 不重试。
DEFAULT_RETRY_DELAYS = (0.5, 1.5)
_RETRYABLE_CODES = {"WEBHOOK_SEND_FAILED", "IM_SEND_FAILED"}


@dataclass
class DeliveryRecord:
    """一次消息投递尝试的结果（docs/56 §4.2），无论成败都落 ring。"""

    id: str
    channel: str
    to: list[str]
    subject: str
    sentAt: str
    status: str  # in_process | delivered:{smtp|webhook|dingtalk|wecom|feishu} | failed
    attempts: int
    elapsedMs: int
    errorCode: str | None = None
    errorMessage: str | None = None


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
        retry_delays: tuple[float, ...] = DEFAULT_RETRY_DELAYS,
        sleep_func: Callable[[float], None] = time.sleep,
    ) -> None:
        # email_sender 需实现 send(to: list[str], subject: str, body: str)，
        # 生产为 message.smtp.SmtpSender；None 时 email 也只记录不投递（demo）。
        self._email_sender = email_sender
        # webhook_sender 需实现 send(url, payload, secret=None)，生产为 message.webhook.DefaultWebhookSender；
        # None 时 webhook 仅进程内记录（demo/测试）。
        self._webhook_sender = webhook_sender
        # im_sender 需实现 send(channel, url, subject, body, secret=None, msg_format=, mentions=)，
        # 生产为 message.im.DefaultImSender；None 时 IM 三渠道仅进程内记录（demo/测试）。
        self._im_sender = im_sender
        self._messages: list[dict[str, object]] = []
        self.last_send: dict[str, object] | None = None
        # docs/56 §4：投递日志 ring（重启/reset 清空）与可注入退避（测试零等待）
        self._deliveries: deque[DeliveryRecord] = deque(maxlen=DELIVERY_RING_SIZE)
        self._retry_delays = tuple(retry_delays)
        self._sleep: Callable[[float], None] = sleep_func

    def send(
        self,
        channel: object,
        to: object,
        subject: object,
        body: object,
        secret: object = None,
        msg_format: object = None,
        mentions: object = None,
    ) -> dict[str, object]:
        channel_value = _require_non_empty(channel, "channel").strip()
        subject_value = _require_non_empty(subject, "subject")
        body_value = _require_non_empty(body, "body")
        secret_value = self._validate_secret(channel_value, secret)
        # docs/58：msg_format/mentions 仅 IM 三渠道生效；webhook/email 忽略。
        im_msg_format = "text"
        im_mentions: dict[str, object] = {"userIds": [], "mobiles": [], "atAll": False}
        if channel_value in IM_CHANNELS:
            if msg_format is None:
                im_msg_format = "text"
            elif isinstance(msg_format, str) and msg_format in ("text", "markdown"):
                im_msg_format = msg_format
            else:
                raise MessageSendError("INVALID_PARAMETER", "msgFormat 必须是 text 或 markdown")
            try:
                im_mentions = normalize_mentions(mentions)
            except ValueError as exc:
                raise MessageSendError("INVALID_PARAMETER", str(exc)) from exc
        # docs/58：webhook 与 IM 渠道支持 1-20 个 URL 的数组（群发，逐目标投递）。
        recipients = _normalize_recipients(to)
        if channel_value == "email" and any("@" not in address for address in recipients):
            raise MessageSendError("INVALID_PARAMETER", "email 渠道的收件地址必须包含 @")

        message_id = str(uuid.uuid4())
        sent_at = datetime.now(timezone.utc).isoformat()
        record: dict[str, object] = {
            "id": message_id,
            "channel": channel_value,
            "to": recipients,
            "subject": subject_value,
            "body": body_value,
            "sent_at": sent_at,
            "delivered": "in_process",
        }
        started = time.monotonic()

        def _log(
            status: str,
            attempts: int,
            code: str | None = None,
            message: str | None = None,
            to: list[str] | None = None,
        ) -> None:
            self._deliveries.append(
                DeliveryRecord(
                    id=message_id,
                    channel=channel_value,
                    to=list(recipients if to is None else to),
                    subject=subject_value[:SUBJECT_LOG_LIMIT],
                    sentAt=sent_at,
                    status=status,
                    attempts=attempts,
                    elapsedMs=int((time.monotonic() - started) * 1000),
                    errorCode=code,
                    errorMessage=(message[:ERROR_LOG_LIMIT] if message else None),
                )
            )

        def _fan_out(transmit_one: Callable[[str], None], delivered_label: str) -> None:
            """逐 URL 投递（docs/58 §4）：单目标内退避、per-URL 投递日志。

            - EGRESS_* fail-fast：出向拦截立即抛、不继续其余 URL；
            - 投递类错误 best-effort：继续其余 URL，发完后聚合抛渠道错误码；
            - 群发层不整体重试（防重复通知）；全成才置 delivered（由调用方写 _messages）。
            """
            failures: list[tuple[str, MessageSendError]] = []
            for target in recipients:
                attempts, error = _transmit(lambda t=target: transmit_one(t))
                if error is not None:
                    _log("failed", attempts, error.code, str(error), to=[target])
                    if error.code.startswith("EGRESS_"):
                        raise error
                    failures.append((target, error))
                else:
                    _log(f"delivered:{delivered_label}", attempts, to=[target])
            if failures:
                raise MessageSendError(
                    failures[0][1].code,
                    f"{channel_value} 群发部分失败：{len(failures)}/{len(recipients)} 个目标投递失败",
                )
            record["delivered"] = delivered_label

        def _transmit(transmit: Callable[[], None]) -> tuple[int, MessageSendError | None]:
            """运行一次真实投递并按可重试错误码退避；返回 (尝试次数, 最终错误|None)。"""
            attempts = 0
            last_error: MessageSendError | None = None
            total = len(self._retry_delays) + 1
            for index in range(total):
                attempts += 1
                try:
                    transmit()
                    return attempts, None
                except MessageSendError as exc:
                    last_error = exc
                    if exc.code in _RETRYABLE_CODES and index < len(self._retry_delays):
                        self._sleep(self._retry_delays[index])
                        continue
                    return attempts, exc
            return attempts, last_error

        if channel_value == "email" and self._email_sender is not None:
            def _email() -> None:
                try:
                    self._email_sender.send(recipients, subject_value, body_value)
                except Exception as exc:
                    # SMTP 不重试：失败折算统一错误码
                    raise MessageSendError("SMTP_SEND_FAILED", f"邮件投递失败：{exc}") from exc

            attempts, error = _transmit(_email)
            if error is not None:
                _log("failed", attempts, error.code, str(error))
                raise error
            record["delivered"] = "smtp"
            _log("delivered:smtp", attempts)
        elif channel_value == "webhook" and self._webhook_sender is not None:
            # 同一 payload（含同一 id 幂等键）发往全部 URL（docs/58 §4.1）。
            payload = {
                "id": message_id,
                "channel": "webhook",
                "subject": subject_value,
                "body": body_value,
                "sent_at": sent_at,
            }

            def _webhook_one(target: str) -> None:
                try:
                    self._webhook_sender.send(target, payload, secret=secret_value)
                except EgressDenied as exc:
                    # SSRF/非法 URL：透传安全码（EGRESS_DENIED/EGRESS_INVALID_URL），不重试
                    raise MessageSendError(exc.code, f"webhook 出向被拦截：{exc}") from exc
                except Exception as exc:
                    # 网络/超时/非 2xx：WEBHOOK_SEND_FAILED，可退避重试
                    raise MessageSendError("WEBHOOK_SEND_FAILED", f"webhook 投递失败：{exc}") from exc

            _fan_out(_webhook_one, "webhook")
        elif channel_value in IM_CHANNELS and self._im_sender is not None:
            fmt = im_msg_format
            mention_payload = im_mentions

            def _im_one(target: str) -> None:
                try:
                    self._im_sender.send(
                        channel_value,
                        target,
                        subject_value,
                        body_value,
                        secret=secret_value,
                        msg_format=fmt,
                        mentions=mention_payload,
                    )
                except EgressDenied as exc:
                    raise MessageSendError(exc.code, f"{channel_value} 出向被拦截：{exc}") from exc
                except Exception as exc:
                    raise MessageSendError("IM_SEND_FAILED", f"IM 投递失败：{exc}") from exc

            _fan_out(_im_one, channel_value)
        else:
            # demo：未注入真实 sender，仅进程内记录、不真实投递
            _log("in_process", 1)
        self._messages.append(record)
        self.last_send = {k: record[k] for k in ("id", "channel", "to", "sent_at")}
        return record

    @staticmethod
    def _validate_secret(channel: str, secret: object) -> str | None:
        # secret：dingtalk/feishu 为机器人加签密钥、webhook 为出站 HMAC 签名密钥（docs/58）；
        # 防止在不支持的渠道（如 wecom，其 webhook URL 自带 key）误以为消息已加签。
        if secret is None or (isinstance(secret, str) and not secret.strip()):
            return None
        if not isinstance(secret, str):
            raise MessageSendError("INVALID_PARAMETER", "secret 必须是字符串")
        value = secret.strip()
        if channel not in ("dingtalk", "feishu", "webhook"):
            raise MessageSendError(
                "INVALID_PARAMETER",
                f"{channel} 渠道不支持 secret（仅 dingtalk/feishu/webhook）",
            )
        if len(value) > MAX_SECRET_LENGTH:
            raise MessageSendError(
                "INVALID_PARAMETER", f"secret 长度上限 {MAX_SECRET_LENGTH} 字符"
            )
        return value

    def list(self) -> list[dict[str, object]]:
        return list(self._messages)

    def list_deliveries(self, limit: int = DELIVERY_LIST_LIMIT) -> list[dict[str, object]]:
        """投递日志倒序（最新在前），limit clamp 1-200（docs/56 §4.3）。"""
        bounded = max(1, min(int(limit), DELIVERY_RING_SIZE))
        recent = list(self._deliveries)[-bounded:]
        return [asdict(item) for item in reversed(recent)]

    def reset(self) -> None:
        self._messages = []
        self._deliveries.clear()
        self.last_send = None

    @property
    def count(self) -> int:
        return len(self._messages)
