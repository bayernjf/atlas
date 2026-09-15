"""进程内消息服务（04 §4.8 权威契约 / 06 §6.7 运行时）。

v1 只记录、不真实投递：send 返回消息记录并保存在进程内列表，重启清空、
reset 清空，GET /api/demo/messages 陪同查看。SMTP/IM/短信/webhook 真实
渠道缓做 docs/14 D24。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

MAX_RECIPIENTS = 20


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
    def __init__(self) -> None:
        self._messages: list[dict[str, object]] = []
        self.last_send: dict[str, object] | None = None

    def send(self, channel: object, to: object, subject: object, body: object) -> dict[str, object]:
        channel_value = _require_non_empty(channel, "channel").strip()
        subject_value = _require_non_empty(subject, "subject")
        body_value = _require_non_empty(body, "body")
        recipients = _normalize_recipients(to)
        if channel_value == "email" and any("@" not in address for address in recipients):
            raise MessageSendError("INVALID_PARAMETER", "email 渠道的收件地址必须包含 @")

        record = {
            "id": str(uuid.uuid4()),
            "channel": channel_value,
            "to": recipients,
            "subject": subject_value,
            "body": body_value,
            "sent_at": datetime.now(timezone.utc).isoformat(),
        }
        self._messages.append(record)
        self.last_send = {k: record[k] for k in ("id", "channel", "to", "sent_at")}
        return record

    def list(self) -> list[dict[str, object]]:
        return list(self._messages)

    def reset(self) -> None:
        self._messages = []
        self.last_send = None

    @property
    def count(self) -> int:
        return len(self._messages)
