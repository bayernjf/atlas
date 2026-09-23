# -*- coding: utf-8 -*-
"""IM 群机器人投递测试（docs/51，D24 IM 子集，候选 U306）。

build_payload/sign_dingtalk_url 固定向量逐字节；DefaultImSender 成功/非 2xx/
平台码非 0/坏 JSON/网络错/EgressDenied 透传；MessageService 三渠道单 URL、
secret 口径、IM_SEND_FAILED、失败不写记录、未注入回退 in_process；adapter schema。
全程 fake 不触网。
"""

from __future__ import annotations

import base64
import hashlib
import hmac as hmac_lib
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from atlas.message.adapter import MessageHarnessAdapter
from atlas.message.im import (
    DefaultImSender,
    ImDeliveryError,
    build_payload,
    sign_dingtalk_url,
)
from atlas.message.service import (
    MAX_SECRET_LENGTH,
    MessageSendError,
    MessageService,
)
from atlas.security.egress import EgressDenied


class FakeGuard:
    def __init__(self, deny_code: str | None = None) -> None:
        self.deny_code = deny_code
        self.checked: list[str] = []

    def check(self, url: str):
        self.checked.append(url)
        if self.deny_code:
            raise EgressDenied(self.deny_code, f"blocked: {url}")
        return SimpleNamespace(host=url)


class FakePost:
    def __init__(self, status: int = 200, body=None, exc: Exception | None = None) -> None:
        self.status = status
        self.body = body if body is not None else {"errcode": 0}
        self.exc = exc
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        if self.exc is not None:
            raise self.exc
        return SimpleNamespace(status_code=self.status, json=lambda: self.body)


# ============================ 纯函数向量 ============================


def test_dingtalk_payload_shape():
    payload = build_payload("dingtalk", "hello", 1700000000, "SEC")
    assert payload == {"msgtype": "text", "text": {"content": "hello"}}


def test_wecom_payload_shape_no_signature():
    payload = build_payload("wecom", "hello", 1700000000, "SEC")
    assert payload == {"msgtype": "text", "text": {"content": "hello"}}


def test_feishu_payload_with_secret_known_vector():
    ts = 1700000000
    secret = "SEC"
    expected_sign = base64.b64encode(
        hmac_lib.new(b"", f"{ts}\n{secret}".encode(), hashlib.sha256).digest()
    ).decode()
    payload = build_payload("feishu", "hello", ts, secret)
    assert payload["msg_type"] == "text"
    assert payload["content"] == {"text": "hello"}
    assert payload["timestamp"] == str(ts)
    assert payload["sign"] == expected_sign


def test_feishu_payload_without_secret_omits_keys():
    payload = build_payload("feishu", "hello", 1700000000, None)
    assert "timestamp" not in payload
    assert "sign" not in payload
    assert payload == {"msg_type": "text", "content": {"text": "hello"}}


def test_sign_dingtalk_url_known_vector():
    ts_ms = 1700000000000
    secret = "SEC"
    expected = base64.b64encode(
        hmac_lib.new(secret.encode(), f"{ts_ms}\n{secret}".encode(), hashlib.sha256).digest()
    ).decode()
    signed = sign_dingtalk_url("https://oapi.dingtalk.com/robot/send?access_token=abc", ts_ms, secret)
    parsed = urlparse(signed)
    query = parse_qs(parsed.query)
    assert query["access_token"] == ["abc"]
    assert query["timestamp"] == [str(ts_ms)]
    assert query["sign"] == [expected]


def test_sign_dingtalk_url_without_existing_query():
    signed = sign_dingtalk_url("https://example.com/hook", 1, "SEC")
    assert signed.startswith("https://example.com/hook?timestamp=1&sign=")


# ============================ DefaultImSender ============================


def test_sender_dingtalk_posts_signed_url():
    post = FakePost(body={"errcode": 0, "errmsg": "ok"})
    sender = DefaultImSender(guard=FakeGuard(), post=post, clock=lambda: 1700000000)
    sender.send("dingtalk", "https://oapi.example/send?access_token=x", "hi", "SEC")
    url, kwargs = post.calls[0]
    assert "timestamp=1700000000000&sign=" in url
    assert kwargs["json"] == {"msgtype": "text", "text": {"content": "hi"}}
    assert kwargs["timeout"] == 10.0


def test_sender_wecom_no_signature():
    post = FakePost(body={"errcode": 0, "errmsg": "ok"})
    sender = DefaultImSender(guard=FakeGuard(), post=post, clock=lambda: 1700000000)
    sender.send("wecom", "https://qyapi.example/cgi-bin/webhook/send?key=x", "hi", None)
    url, kwargs = post.calls[0]
    assert url == "https://qyapi.example/cgi-bin/webhook/send?key=x"
    assert kwargs["json"] == {"msgtype": "text", "text": {"content": "hi"}}


def test_sender_feishu_signed_body():
    post = FakePost(body={"code": 0, "msg": "success"})
    sender = DefaultImSender(guard=FakeGuard(), post=post, clock=lambda: 1700000000)
    sender.send("feishu", "https://open.feishu.example/open-apis/bot/v2/hook/x", "hi", "SEC")
    _, kwargs = post.calls[0]
    body = kwargs["json"]
    assert body["timestamp"] == "1700000000"
    assert body["msg_type"] == "text"
    assert body["content"] == {"text": "hi"}
    assert isinstance(body["sign"], str)


def test_sender_dingtalk_no_secret_unsigned():
    post = FakePost(body={"errcode": 0})
    sender = DefaultImSender(guard=FakeGuard(), post=post, clock=lambda: 1700000000)
    sender.send("dingtalk", "https://oapi.example/send?access_token=x", "hi", None)
    url, _ = post.calls[0]
    assert url == "https://oapi.example/send?access_token=x"


@pytest.mark.parametrize(
    "channel,body",
    [
        ("dingtalk", {"errcode": 310000, "errmsg": "sign not match"}),
        ("wecom", {"errcode": 93000, "errmsg": "invalid webhook url"}),
        ("feishu", {"code": 19021, "msg": "sign match fail"}),
    ],
)
def test_sender_platform_error_code_fails(channel, body):
    post = FakePost(status=200, body=body)
    sender = DefaultImSender(guard=FakeGuard(), post=post)
    with pytest.raises(ImDeliveryError, match="平台错误码"):
        sender.send(channel, "https://example.com/hook", "hi", None)


def test_sender_non_2xx_fails():
    post = FakePost(status=500)
    sender = DefaultImSender(guard=FakeGuard(), post=post)
    with pytest.raises(ImDeliveryError, match="非 2xx"):
        sender.send("wecom", "https://example.com/hook", "hi", None)


def test_sender_bad_json_fails():
    def post(url, **kwargs):
        return SimpleNamespace(
            status_code=200, json=lambda: (_ for _ in ()).throw(ValueError("no json"))
        )

    sender = DefaultImSender(guard=FakeGuard(), post=post)
    with pytest.raises(ImDeliveryError, match="非 JSON"):
        sender.send("wecom", "https://example.com/hook", "hi", None)


def test_sender_network_error_fails():
    post = FakePost(exc=httpx.ConnectError("boom"))
    sender = DefaultImSender(guard=FakeGuard(), post=post)
    with pytest.raises(ImDeliveryError, match="请求失败"):
        sender.send("dingtalk", "https://example.com/hook", "hi", None)


def test_sender_egress_denied_passthrough():
    guard = FakeGuard(deny_code="EGRESS_DENIED")
    sender = DefaultImSender(guard=guard, post=FakePost())
    with pytest.raises(EgressDenied):
        sender.send("wecom", "http://127.0.0.1/hook", "hi", None)


# ============================ MessageService ============================


class FakeImSender:
    def __init__(self, exc: Exception | None = None) -> None:
        self.exc = exc
        self.calls: list[tuple] = []

    def send(self, channel, url, text, secret):
        self.calls.append((channel, url, text, secret))
        if self.exc is not None:
            raise self.exc


@pytest.mark.parametrize("channel", ["dingtalk", "wecom", "feishu"])
def test_service_im_success_marks_channel(channel):
    fake = FakeImSender()
    service = MessageService(im_sender=fake)
    record = service.send(channel, "https://example.com/hook", "标题", "正文")
    assert record["delivered"] == channel
    assert record["to"] == ["https://example.com/hook"]
    sent_channel, url, text, secret = fake.calls[0]
    assert sent_channel == channel
    assert url == "https://example.com/hook"
    assert text == "标题\n正文"
    assert secret is None


def test_service_dingtalk_secret_passed_through():
    fake = FakeImSender()
    service = MessageService(im_sender=fake)
    service.send("dingtalk", "https://example.com/hook", "s", "b", secret=" SEC ")
    assert fake.calls[0][3] == "SEC"


def test_service_feishu_secret_passed_through():
    fake = FakeImSender()
    service = MessageService(im_sender=fake)
    service.send("feishu", "https://example.com/hook", "s", "b", secret="SEC")
    assert fake.calls[0][3] == "SEC"


@pytest.mark.parametrize("channel", ["dingtalk", "wecom", "feishu"])
def test_service_im_array_to_invalid(channel):
    service = MessageService(im_sender=FakeImSender())
    with pytest.raises(MessageSendError) as exc:
        service.send(channel, ["https://example.com/hook"], "s", "b")
    assert exc.value.code == "INVALID_PARAMETER"


@pytest.mark.parametrize("channel", ["wecom", "email", "webhook", "sms"])
def test_service_secret_unsupported_channel_invalid(channel):
    service = MessageService(im_sender=FakeImSender())
    to = "https://example.com/hook" if channel != "email" else "ops@example.com"
    with pytest.raises(MessageSendError) as exc:
        service.send(channel, to, "s", "b", secret="SEC")
    assert exc.value.code == "INVALID_PARAMETER"


def test_service_secret_non_string_invalid():
    service = MessageService(im_sender=FakeImSender())
    with pytest.raises(MessageSendError) as exc:
        service.send("dingtalk", "https://example.com/hook", "s", "b", secret=123)
    assert exc.value.code == "INVALID_PARAMETER"


def test_service_secret_too_long_invalid():
    service = MessageService(im_sender=FakeImSender())
    with pytest.raises(MessageSendError) as exc:
        service.send(
            "dingtalk", "https://example.com/hook", "s", "b", secret="x" * (MAX_SECRET_LENGTH + 1)
        )
    assert exc.value.code == "INVALID_PARAMETER"


def test_service_im_failure_no_record():
    fake = FakeImSender(exc=ImDeliveryError("平台错误码 5"))
    service = MessageService(im_sender=fake)
    with pytest.raises(MessageSendError) as exc:
        service.send("dingtalk", "https://example.com/hook", "s", "b")
    assert exc.value.code == "IM_SEND_FAILED"
    assert service.count == 0


def test_service_im_egress_denied_passthrough():
    fake = FakeImSender(exc=EgressDenied("EGRESS_DENIED", "blocked"))
    service = MessageService(im_sender=fake)
    with pytest.raises(MessageSendError) as exc:
        service.send("wecom", "http://127.0.0.1/hook", "s", "b")
    assert exc.value.code == "EGRESS_DENIED"
    assert service.count == 0


def test_service_im_no_sender_falls_back_in_process():
    service = MessageService()
    record = service.send("dingtalk", "https://example.com/hook", "s", "b", secret="SEC")
    assert record["delivered"] == "in_process"


def test_service_other_channels_zero_regression():
    service = MessageService()
    email_record = service.send("email", "ops@example.com", "s", "b")
    webhook_record = service.send("webhook", "https://example.com/hook", "s", "b")
    sms_record = service.send("sms", "10086", "s", "b")
    assert [r["delivered"] for r in (email_record, webhook_record, sms_record)] == [
        "in_process",
        "in_process",
        "in_process",
    ]


# ============================ adapter schema ============================


def test_adapter_input_schema_has_secret():
    adapter = MessageHarnessAdapter()
    capability = adapter.list_capabilities()[0]
    props = capability.input_schema["properties"]
    assert "secret" in props
    assert props["secret"]["maxLength"] == MAX_SECRET_LENGTH
    assert "dingtalk" in props["channel"]["description"]
    assert "secret" not in capability.input_schema["required"]
