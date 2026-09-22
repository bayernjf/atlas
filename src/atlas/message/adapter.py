"""进程内消息 Harness 适配器（adapter_id="message", adapter_type="message"）。

单能力 message/send（write，非幂等）；参数契约见 04 §4.8 权威 blockquote，
运行时语义见 06 §6.7。v1 无真实投递。
"""

from __future__ import annotations

from atlas.harness.base import (
    ActionRequest,
    ActionResult,
    Capability,
    HarnessAdapter,
    Observation,
    Permission,
    StructuredError,
)
from .service import MAX_RECIPIENTS, MessageSendError, MessageService

_SEND_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "channel": {"type": "string", "description": "email（SMTP 真实发信）/webhook（向单个 URL POST JSON，过 SSRF 校验）真实投递；sms/im 或其他标识仅进程内记录（v1 不路由）"},
        "to": {
            "description": "收件人字符串或字符串数组（群发上限 20；email 渠道须含 @）",
            "oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}, "maxItems": MAX_RECIPIENTS}],
        },
        "subject": {"type": "string"},
        "body": {"type": "string"},
    },
    "required": ["channel", "to", "subject", "body"],
}

_SEND_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "channel": {"type": "string"},
        "to": {"type": "array", "items": {"type": "string"}},
        "subject": {"type": "string"},
        "body": {"type": "string"},
        "sent_at": {"type": "string"},
    },
    "required": ["id", "channel", "to", "subject", "body", "sent_at"],
}


class MessageHarnessAdapter(HarnessAdapter):
    adapter_id = "message"
    adapter_type = "message"

    def __init__(self, service: MessageService | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.service = service or MessageService()

    def list_capabilities(self) -> list[Capability]:
        return [
            Capability(
                name="send",
                description="发送进程内演示消息（仅记录不投递，群发上限 20）",
                action="message_send",
                input_schema=_SEND_INPUT_SCHEMA,
                output_schema=_SEND_OUTPUT_SCHEMA,
                permission=Permission.WRITE,
                timeout=30.0,
                is_idempotent=False,
            ),
        ]

    def _execute(self, request: ActionRequest) -> ActionResult:
        if request.capability_name != "send":
            return ActionResult.failed(
                StructuredError("UNKNOWN_CAPABILITY", f"未知能力：{request.capability_name}")
            )
        params = request.parameters
        try:
            output = self.service.send(
                channel=params.get("channel"),
                to=params.get("to"),
                subject=params.get("subject"),
                body=params.get("body"),
            )
        except MessageSendError as exc:
            return ActionResult.failed(StructuredError(exc.code, str(exc)))
        return ActionResult.success(output)

    def observe(self) -> Observation:
        return Observation(
            url="obs://message/in-process",
            title="消息适配器（进程内消息服务）",
            data={"count": self.service.count, "last_send": self.service.last_send},
        )
