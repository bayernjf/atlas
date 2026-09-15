"""消息适配器（进程内消息服务）单元测试（13 文档 U26/I14）。

零真实投递：send 只落进程内记录。
"""

from __future__ import annotations

import pytest

from atlas.harness.base import ActionRequest, ActionStatus, Permission
from atlas.message.adapter import MessageHarnessAdapter
from atlas.message.service import MAX_RECIPIENTS, MessageSendError, MessageService


def make_adapter(service=None, granted=None) -> MessageHarnessAdapter:
    return MessageHarnessAdapter(
        service=service or MessageService(),
        granted_permissions=granted
        or {Permission.READ, Permission.WRITE, Permission.DELETE, Permission.FINANCIAL},
    )


# --- service ---

def test_send_string_to_flattens_record():
    record = MessageService().send("email", "ops@example.com", "标题", "正文")

    assert record["to"] == ["ops@example.com"]
    assert record["channel"] == "email"
    assert record["subject"] == "标题"
    assert record["body"] == "正文"
    assert isinstance(record["id"], str) and len(record["id"]) == 36
    assert record["sent_at"].endswith("+00:00")


def test_send_list_to_preserves_order():
    service = MessageService()

    record = service.send("sms", ["a", "b"], "s", "b")

    assert record["to"] == ["a", "b"]
    assert service.list() == [record]
    assert service.count == 1


def test_send_over_group_limit_rejected():
    with pytest.raises(MessageSendError) as exc_info:
        MessageService().send("sms", [f"u{i}" for i in range(MAX_RECIPIENTS + 1)], "s", "b")

    assert exc_info.value.code == "INVALID_PARAMETER"


def test_email_channel_requires_at_shape():
    with pytest.raises(MessageSendError) as exc_info:
        MessageService().send("email", "not-an-address", "s", "b")

    assert exc_info.value.code == "INVALID_PARAMETER"


def test_non_email_channel_skips_at_check():
    record = MessageService().send("im", "张三", "s", "b")

    assert record["to"] == ["张三"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"channel": "", "to": "a@b.com", "subject": "s", "body": "b"},
        {"channel": "email", "to": "", "subject": "s", "body": "b"},
        {"channel": "email", "to": "a@b.com", "subject": "  ", "body": "b"},
        {"channel": "email", "to": "a@b.com", "subject": "s", "body": ""},
        {"channel": "email", "to": 123, "subject": "s", "body": "b"},
        {"channel": "email", "to": [1, 2], "subject": "s", "body": "b"},
        {"channel": "email", "to": ["a@b.com", 1], "subject": "s", "body": "b"},
    ],
)
def test_invalid_parameters_rejected(kwargs):
    with pytest.raises(MessageSendError) as exc_info:
        MessageService().send(**kwargs)

    assert exc_info.value.code in {"MISSING_PARAMETER", "INVALID_PARAMETER"}


def test_reset_clears_records():
    service = MessageService()
    service.send("im", "u", "s", "b")

    service.reset()

    assert service.list() == []
    assert service.count == 0
    assert service.last_send is None


def test_blank_recipients_filtered_then_missing():
    with pytest.raises(MessageSendError) as exc_info:
        MessageService().send("im", ["  "], "s", "b")

    assert exc_info.value.code == "MISSING_PARAMETER"


# --- adapter ---

def test_adapter_declares_single_write_capability():
    capabilities = make_adapter().list_capabilities()

    assert [(c.name, c.permission, c.is_idempotent) for c in capabilities] == [
        ("send", Permission.WRITE, False)
    ]


def test_adapter_send_success_and_visible():
    service = MessageService()
    result = make_adapter(service=service).execute(
        ActionRequest(
            capability_name="send",
            parameters={"channel": "email", "to": ["a@b.com"], "subject": "通知", "body": "内容"},
        )
    )

    assert result.status is ActionStatus.SUCCESS
    assert result.output["id"] == service.list()[0]["id"]


def test_adapter_send_validation_error():
    result = make_adapter().execute(
        ActionRequest(capability_name="send", parameters={"channel": "email", "to": "bad"})
    )

    assert result.status is ActionStatus.FAILED
    assert result.error.code in {"MISSING_PARAMETER", "INVALID_PARAMETER"}


def test_adapter_send_bad_email_shape():
    result = make_adapter().execute(
        ActionRequest(
            capability_name="send",
            parameters={"channel": "email", "to": "bad-address", "subject": "s", "body": "b"},
        )
    )

    assert result.status is ActionStatus.FAILED
    assert result.error.code == "INVALID_PARAMETER"


def test_adapter_requires_write_permission_with_audit():
    audited = []
    adapter = MessageHarnessAdapter(
        service=MessageService(),
        granted_permissions={Permission.READ},
        audit_sink=lambda adapter_id, name, req: audited.append((adapter_id, name)),
    )

    result = adapter.execute(
        ActionRequest(
            capability_name="send",
            parameters={"channel": "im", "to": "u", "subject": "s", "body": "b"},
        )
    )

    assert result.status is ActionStatus.FAILED
    assert result.error.code == "PERMISSION_DENIED"
    assert audited == [("message", "send")]


def test_adapter_unknown_capability():
    result = make_adapter().execute(ActionRequest(capability_name="nope", parameters={}))

    assert result.status is ActionStatus.FAILED
    assert result.error.code == "UNKNOWN_CAPABILITY"


def test_adapter_observe_reports_count_and_last_send():
    adapter = make_adapter()
    adapter.execute(
        ActionRequest(
            capability_name="send",
            parameters={"channel": "im", "to": "u", "subject": "s", "body": "b"},
        )
    )

    observation = adapter.observe()

    assert observation.url == "obs://message/in-process"
    assert observation.data["count"] == 1
    assert observation.data["last_send"]["channel"] == "im"
