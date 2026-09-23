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
from .service import MAX_RECIPIENTS, MAX_SECRET_LENGTH, MessageSendError, MessageService

_SEND_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "channel": {"type": "string", "description": "真实投递：email（SMTP）/webhook（单 URL POST JSON）/dingtalk/wecom/feishu（群机器人，均过 SSRF 校验）；sms 或其他标识仅进程内记录（v1 不路由）"},
        "to": {
            "description": "收件人字符串或字符串数组（群发上限 20；email 渠道须含 @）；webhook 与 IM 渠道为单个 URL",
            "oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}, "maxItems": MAX_RECIPIENTS}],
        },
        "subject": {"type": "string"},
        "body": {"type": "string"},
        "secret": {"type": "string", "description": "dingtalk/feishu 为群机器人加签密钥、webhook 为出站 HMAC-SHA256 签名密钥（X-Atlas-Signature，docs/58）；wecom 不支持；留空不加签；运行时参数（生产应由 secret provider 注入）", "maxLength": MAX_SECRET_LENGTH},
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
                description="发送消息：email/webhook/dingtalk/wecom/feishu 真实投递（IM 与 webhook 单 URL、过 SSRF 校验），其余渠道仅进程内记录；群发上限 20",
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
                secret=params.get("secret"),
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
