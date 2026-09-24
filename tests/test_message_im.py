# -*- coding: utf-8 -*-
"""IM 群机器人投递测试（docs/51 v1；docs/58 markdown 富文本与 @人）。

build_payload/sign_dingtalk_url 固定向量逐字节（text/markdown × 三家 @人语义）；
normalize_mentions 归一化；DefaultImSender 成功/非 2xx/平台码非 0/坏 JSON/网络错/
EgressDenied 透传；MessageService 三渠道单 URL、secret 口径、msgFormat/mentions 校验与
透传、IM_SEND_FAILED、失败不写记录、未注入回退 in_process；adapter schema。全程 fake 不触网。
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
    normalize_mentions,
    sign_dingtalk_url,
)
from atlas.message.service import (
    MAX_RECIPIENTS,
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


# ============================ normalize_mentions ============================


def test_normalize_mentions_none_default():
    assert normalize_mentions(None) == {"userIds": [], "mobiles": [], "atAll": False}


def test_normalize_mentions_strips_dedupes_and_preserves_order():
    out = normalize_mentions(
        {"userIds": [" u1 ", "u2", "u1", "", " u2 "], "mobiles": [" 138 ", "138"], "atAll": False}
    )
    assert out == {"userIds": ["u1", "u2"], "mobiles": ["138"], "atAll": False}


def test_normalize_mentions_at_all_true():
    out = normalize_mentions({"atAll": True})
    assert out == {"userIds": [], "mobiles": [], "atAll": True}


@pytest.mark.parametrize(
    "bad",
    [
        ["not", "dict"],
        {"userIds": "x"},
        {"userIds": [1]},
        {"mobiles": "x"},
        {"atAll": "yes"},
        {"unknown": 1},
        {"userIds": [f"u{i}" for i in range(MAX_RECIPIENTS + 1)]},
    ],
)
def test_normalize_mentions_rejects_bad_shapes(bad):
    with pytest.raises(ValueError):
        normalize_mentions(bad)


# ============================ 纯函数向量：text（向后兼容） ============================


def test_dingtalk_text_payload_shape():
    payload = build_payload("dingtalk", "标题", "正文", 1700000000, None)
    assert payload == {
        "msgtype": "text",
        "text": {"content": "标题\n正文"},
        "at": {"atMobiles": [], "atUserIds": [], "isAtAll": False},
    }


def test_wecom_text_payload_shape():
    payload = build_payload("wecom", "标题", "正文", 1700000000, None)
    assert payload == {
        "msgtype": "text",
        "text": {
            "content": "标题\n正文",
            "mentioned_list": [],
            "mentioned_mobile_list": [],
        },
    }


def test_feishu_text_payload_with_secret_known_vector():
    ts = 1700000000
    secret = "SEC"
    expected_sign = base64.b64encode(
        hmac_lib.new(b"", f"{ts}\n{secret}".encode(), hashlib.sha256).digest()
    ).decode()
    payload = build_payload("feishu", "标题", "正文", ts, secret)
    assert payload["msg_type"] == "text"
    assert payload["content"] == {"text": "标题\n正文"}
    assert payload["timestamp"] == str(ts)
    assert payload["sign"] == expected_sign


def test_feishu_text_payload_without_secret_omits_keys():
    payload = build_payload("feishu", "标题", "正文", 1700000000, None)
    assert "timestamp" not in payload
    assert "sign" not in payload
    assert payload == {"msg_type": "text", "content": {"text": "标题\n正文"}}


# ============================ 纯函数向量：@人 text ============================


def test_dingtalk_text_mentions_appends_at_tokens_and_block():
    payload = build_payload(
        "dingtalk", "标题", "正文", 1700000000, None,
        mentions={"userIds": ["uid1"], "mobiles": ["13800000000"]},
    )
    assert payload["text"]["content"] == "标题\n正文\n@13800000000 @uid1"
    assert payload["at"] == {
        "atMobiles": ["13800000000"],
        "atUserIds": ["uid1"],
        "isAtAll": False,
    }


def test_dingtalk_text_at_all_no_inline_tokens():
    payload = build_payload(
        "dingtalk", "标题", "正文", 1700000000, None, mentions={"atAll": True}
    )
    assert payload["text"]["content"] == "标题\n正文"  # isAtAll 生效，正文不拼 @
    assert payload["at"]["isAtAll"] is True


def test_wecom_text_mentions_lists_and_at_all_token():
    payload = build_payload(
        "wecom", "标题", "正文", 1700000000, None,
        mentions={"userIds": ["zhangsan"], "mobiles": ["13800000000"], "atAll": True},
    )
    assert payload["text"]["mentioned_list"] == ["zhangsan", "@all"]
    assert payload["text"]["mentioned_mobile_list"] == ["13800000000", "@all"]


def test_feishu_text_mentions_inline_tags_ignores_mobiles():
    payload = build_payload(
        "feishu", "标题", "正文", 1700000000, None,
        mentions={"userIds": ["ou_xxx"], "mobiles": ["13800000000"]},
    )
    text = payload["content"]["text"]
    assert text == '标题\n正文 <at user_id="ou_xxx">ou_xxx</at>'
    assert "13800000000" not in text  # 飞书无手机号 @能力


def test_feishu_text_at_all_tag():
    payload = build_payload("feishu", "标题", "正文", 1700000000, None, mentions={"atAll": True})
    assert payload["content"]["text"] == '标题\n正文 <at user_id="all">所有人</at>'


# ============================ 纯函数向量：markdown ============================


def test_dingtalk_markdown_payload_vector():
    payload = build_payload(
        "dingtalk", "告警标题", "**失败率** 0.2", 1700000000, None,
        msg_format="markdown", mentions={"userIds": ["uid1"], "mobiles": ["13800000000"]},
    )
    assert payload == {
        "msgtype": "markdown",
        "markdown": {"title": "告警标题", "text": "**失败率** 0.2\n@13800000000 @uid1"},
        "at": {"atMobiles": ["13800000000"], "atUserIds": ["uid1"], "isAtAll": False},
    }


def test_wecom_markdown_payload_inline_userid_tags_ignores_mobiles():
    payload = build_payload(
        "wecom", "告警标题", "**失败率** 0.2", 1700000000, None,
        msg_format="markdown",
        mentions={"userIds": ["zhangsan", "lisi"], "mobiles": ["13800000000"], "atAll": True},
    )
    # markdown 体无 mentioned_* 字段；mobiles 无字段可用（忽略）
    assert payload == {
        "msgtype": "markdown",
        "markdown": {"content": "**失败率** 0.2 <@zhangsan> <@lisi> <@all>"},
    }


def test_feishu_post_payload_splits_lines_and_appends_at_paragraph():
    payload = build_payload(
        "feishu", "告警标题", "**行1**\n\n行2", 1700000000, None,
        msg_format="markdown", mentions={"userIds": ["ou_a", "ou_b"]},
    )
    assert payload["msg_type"] == "post"
    zh = payload["content"]["post"]["zh_cn"]
    assert zh["title"] == "告警标题"
    # 空行跳过；@人成为最后一个段落
    assert zh["content"] == [
        [{"tag": "text", "text": "**行1**"}],
        [{"tag": "text", "text": "行2"}],
        [{"tag": "at", "user_id": "ou_a"}, {"tag": "at", "user_id": "ou_b"}],
    ]


def test_feishu_post_at_all_paragraph():
    payload = build_payload(
        "feishu", "标题", "正文", 1700000000, "SEC", msg_format="markdown",
        mentions={"atAll": True},
    )
    last = payload["content"]["post"]["zh_cn"]["content"][-1]
    assert last == [{"tag": "at", "user_id": "all", "user_name": "所有人"}]
    assert payload["timestamp"] == "1700000000"  # 加签与 msg_type 无关


def test_feishu_post_empty_body_only_at_paragraph():
    payload = build_payload(
        "feishu", "标题", " \n ", 1700000000, None, msg_format="markdown",
        mentions={"userIds": ["ou_a"]},
    )
    assert payload["content"]["post"]["zh_cn"]["content"] == [
        [{"tag": "at", "user_id": "ou_a"}]
    ]


def test_build_payload_unknown_format_or_channel():
    with pytest.raises(ImDeliveryError):
        build_payload("dingtalk", "s", "b", 1, None, msg_format="html")
    with pytest.raises(ImDeliveryError):
        build_payload("sms", "s", "b", 1, None)


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


def test_sender_dingtalk_posts_signed_url_markdown():
    post = FakePost(body={"errcode": 0, "errmsg": "ok"})
    sender = DefaultImSender(guard=FakeGuard(), post=post, clock=lambda: 1700000000)
    sender.send(
        "dingtalk", "https://oapi.example/send?access_token=x",
        "标题", "**正文**", "SEC", msg_format="markdown",
        mentions={"userIds": ["uid1"]},
    )
    url, kwargs = post.calls[0]
    assert "timestamp=1700000000000&sign=" in url
    assert kwargs["json"]["msgtype"] == "markdown"
    assert kwargs["json"]["markdown"]["title"] == "标题"
    assert kwargs["json"]["at"]["atUserIds"] == ["uid1"]
    assert kwargs["timeout"] == 10.0


def test_sender_wecom_text_no_signature():
    post = FakePost(body={"errcode": 0, "errmsg": "ok"})
    sender = DefaultImSender(guard=FakeGuard(), post=post, clock=lambda: 1700000000)
    sender.send("wecom", "https://qyapi.example/cgi-bin/webhook/send?key=x", "标题", "正文", None)
    url, kwargs = post.calls[0]
    assert url == "https://qyapi.example/cgi-bin/webhook/send?key=x"
    assert kwargs["json"]["msgtype"] == "text"
    assert kwargs["json"]["text"]["content"] == "标题\n正文"


def test_sender_feishu_signed_post_body():
    post = FakePost(body={"code": 0, "msg": "success"})
    sender = DefaultImSender(guard=FakeGuard(), post=post, clock=lambda: 1700000000)
    sender.send("feishu", "https://open.feishu.example/open-apis/bot/v2/hook/x", "标题", "正文", "SEC")
    _, kwargs = post.calls[0]
    body = kwargs["json"]
    assert body["timestamp"] == "1700000000"
    assert body["msg_type"] == "text"
    assert body["content"] == {"text": "标题\n正文"}
    assert isinstance(body["sign"], str)


def test_sender_dingtalk_no_secret_unsigned():
    post = FakePost(body={"errcode": 0})
    sender = DefaultImSender(guard=FakeGuard(), post=post, clock=lambda: 1700000000)
    sender.send("dingtalk", "https://oapi.example/send?access_token=x", "标题", "正文", None)
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
        sender.send(channel, "https://example.com/hook", "s", "b")


def test_sender_non_2xx_fails():
    post = FakePost(status=500)
    sender = DefaultImSender(guard=FakeGuard(), post=post)
    with pytest.raises(ImDeliveryError, match="非 2xx"):
        sender.send("wecom", "https://example.com/hook", "s", "b")


def test_sender_bad_json_fails():
    def post(url, **kwargs):
        return SimpleNamespace(
            status_code=200, json=lambda: (_ for _ in ()).throw(ValueError("no json"))
        )

    sender = DefaultImSender(guard=FakeGuard(), post=post)
    with pytest.raises(ImDeliveryError, match="非 JSON"):
        sender.send("wecom", "https://example.com/hook", "s", "b")


def test_sender_network_error_fails():
    post = FakePost(exc=httpx.ConnectError("boom"))
    sender = DefaultImSender(guard=FakeGuard(), post=post)
    with pytest.raises(ImDeliveryError, match="请求失败"):
        sender.send("dingtalk", "https://example.com/hook", "s", "b")


def test_sender_egress_denied_passthrough():
    guard = FakeGuard(deny_code="EGRESS_DENIED")
    sender = DefaultImSender(guard=guard, post=FakePost())
    with pytest.raises(EgressDenied):
        sender.send("wecom", "http://127.0.0.1/hook", "s", "b")


def test_sender_unknown_channel_fails():
    sender = DefaultImSender(guard=FakeGuard(), post=FakePost())
    with pytest.raises(ImDeliveryError, match="未知 IM 渠道"):
        sender.send("sms", "https://example.com/hook", "s", "b")


# ============================ MessageService ============================


class FakeImSender:
    def __init__(self, exc: Exception | None = None) -> None:
        self.exc = exc
        self.calls: list[tuple] = []

    def send(self, channel, url, subject, body, secret=None, msg_format="text", mentions=None):
        self.calls.append((channel, url, subject, body, secret, msg_format, mentions))
        if self.exc is not None:
            raise self.exc


@pytest.mark.parametrize("channel", ["dingtalk", "wecom", "feishu"])
def test_service_im_success_marks_channel(channel):
    fake = FakeImSender()
    service = MessageService(im_sender=fake)
    record = service.send(channel, "https://example.com/hook", "标题", "正文")
    assert record["delivered"] == channel
    assert record["to"] == ["https://example.com/hook"]
    sent_channel, url, subject, body, secret, fmt, mentions = fake.calls[0]
    assert (sent_channel, url, subject, body) == (channel, "https://example.com/hook", "标题", "正文")
    assert secret is None and fmt == "text"
    assert mentions == {"userIds": [], "mobiles": [], "atAll": False}


def test_service_im_markdown_and_mentions_passed_through():
    fake = FakeImSender()
    service = MessageService(im_sender=fake)
    service.send(
        "dingtalk", "https://example.com/hook", "标题", "**正文**",
        msg_format="markdown", mentions={"userIds": [" u1 "], "atAll": False},
    )
    _, _, _, _, secret, fmt, mentions = fake.calls[0]
    assert secret is None and fmt == "markdown"
    assert mentions == {"userIds": ["u1"], "mobiles": [], "atAll": False}


@pytest.mark.parametrize("bad_fmt", ["html", "", 123])
def test_service_im_bad_msg_format_invalid(bad_fmt):
    fake = FakeImSender()
    service = MessageService(im_sender=fake)
    with pytest.raises(MessageSendError) as exc:
        service.send("dingtalk", "https://example.com/hook", "s", "b", msg_format=bad_fmt)
    assert exc.value.code == "INVALID_PARAMETER"
    assert fake.calls == []


@pytest.mark.parametrize(
    "bad_mentions",
    [["u1"], {"userIds": [1]}, {"atAll": "yes"}, {"userIds": [f"u{i}" for i in range(21)]}],
)
def test_service_im_bad_mentions_invalid(bad_mentions):
    fake = FakeImSender()
    service = MessageService(im_sender=fake)
    with pytest.raises(MessageSendError) as exc:
        service.send("feishu", "https://example.com/hook", "s", "b", mentions=bad_mentions)
    assert exc.value.code == "INVALID_PARAMETER"
    assert fake.calls == []


def test_service_webhook_ignores_mentions_and_format():
    # msg_format/mentions 对 webhook 不报错也不透传（契约：webhook/email 忽略）
    svc = MessageService()
    rec = svc.send(
        "webhook", "https://example.com/hook", "s", "b",
        msg_format="markdown", mentions={"atAll": True},
    )
    assert rec["delivered"] == "in_process"


def test_service_dingtalk_secret_passed_through():
    fake = FakeImSender()
    service = MessageService(im_sender=fake)
    service.send("dingtalk", "https://example.com/hook", "s", "b", secret=" SEC ")
    assert fake.calls[0][4] == "SEC"


def test_service_feishu_secret_passed_through():
    fake = FakeImSender()
    service = MessageService(im_sender=fake)
    service.send("feishu", "https://example.com/hook", "s", "b", secret="SEC")
    assert fake.calls[0][4] == "SEC"


@pytest.mark.parametrize("channel", ["dingtalk", "wecom", "feishu"])
def test_service_im_multiple_urls_all_succeed(channel):
    """docs/58 §4：IM 多 URL 群发，逐目标投递、一条消息记录。"""
    fake = FakeImSender()
    service = MessageService(im_sender=fake)
    urls = ["https://example.com/a", "https://example.com/b"]
    rec = service.send(channel, urls, "标题", "**正文**", msg_format="markdown")
    assert rec["delivered"] == channel and rec["to"] == urls
    assert service.count == 1
    assert [c[1] for c in fake.calls] == urls
    assert all(c[5] == "markdown" for c in fake.calls)
    deliveries = service.list_deliveries()
    assert {tuple(d["to"]) for d in deliveries} == {
        ("https://example.com/a",), ("https://example.com/b",)
    }
    assert all(d["status"] == f"delivered:{channel}" for d in deliveries)


class RoutingImSender:
    def __init__(self, fail_map: dict[str, Exception]) -> None:
        self.fail_map = fail_map
        self.calls: list[str] = []

    def send(self, channel, url, subject, body, secret=None, msg_format="text", mentions=None):
        self.calls.append(url)
        if url in self.fail_map:
            raise self.fail_map[url]


def test_service_im_partial_failure_best_effort_and_egress_fail_fast():
    ok = "https://example.com/ok"
    bad = "https://example.com/bad"
    denied = "http://127.0.0.1/hook"
    # 半败：投递错误 best-effort 发完其余
    fake = RoutingImSender({bad: ImDeliveryError("平台错误码 5")})
    svc = MessageService(im_sender=fake, retry_delays=(0, 0), sleep_func=lambda _s: None)
    with pytest.raises(MessageSendError) as ei:
        svc.send("dingtalk", [ok, bad], "s", "b")
    assert ei.value.code == "IM_SEND_FAILED" and "1/2" in str(ei.value)
    # best-effort：ok 一次，bad 单目标内重试 3 次
    assert fake.calls == [ok, bad, bad, bad] and svc.count == 0
    # EGRESS fail-fast：拦截即停
    fake2 = RoutingImSender({denied: EgressDenied("EGRESS_DENIED", "blocked")})
    svc2 = MessageService(im_sender=fake2)
    with pytest.raises(MessageSendError) as ei:
        svc2.send("wecom", [denied, ok], "s", "b")
    assert ei.value.code == "EGRESS_DENIED"
    assert fake2.calls == [denied]


@pytest.mark.parametrize("channel", ["wecom", "email", "sms"])
def test_service_secret_unsupported_channel_invalid(channel):
    # docs/58 起 webhook 支持 secret（出站 HMAC 签名），不在不支持列表
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


def test_adapter_input_schema_has_secret_and_rich_fields():
    adapter = MessageHarnessAdapter()
    capability = adapter.list_capabilities()[0]
    props = capability.input_schema["properties"]
    assert "secret" in props
    assert props["secret"]["maxLength"] == MAX_SECRET_LENGTH
    assert "dingtalk" in props["channel"]["description"]
    assert "secret" not in capability.input_schema["required"]
    # docs/58：msgFormat/mentions
    assert props["msgFormat"]["enum"] == ["text", "markdown"]
    mentions = props["mentions"]
    assert mentions["additionalProperties"] is False
    assert set(mentions["properties"].keys()) == {"userIds", "mobiles", "atAll"}
    assert mentions["properties"]["userIds"]["maxItems"] == MAX_RECIPIENTS
