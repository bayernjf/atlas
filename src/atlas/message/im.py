"""IM 群机器人消息投递（dingtalk / wecom / feishu；docs/51 v1，docs/58 富文本与 @人）。

三个渠道均为「自定义群机器人 webhook」形态：目标 URL 先过 SSRF 出向校验，再 POST
平台约定的 JSON 消息体；不跟随重定向，10s 超时。平台返回业务错误码（钉钉/企微 errcode、
飞书 code）非 0 视为投递失败。

- 钉钉：URL query 加签（HMAC-SHA256，secret 作 key 与 base 后缀）；text/markdown + at 块。
- 企微：webhook URL 自带 key，无加签；text 用 mentioned_* 字段，markdown 在 content 内拼
  `<@userid>`/`<@all>` 扩展语法（markdown 体无 mentioned_* 字段）。
- 飞书：body 加签（空 key HMAC-SHA256）；text 内联 `<at>` 标签，markdown 模式落 post 富文本。

消息体以三家官方文档为准（docs/58 §3.2 列来源 URL）。v1 不做应用机器人 OAuth、@手机号
飞书支持（平台无此能力，忽略）、interactive 卡片（缓做 docs/14 D24）。
"""

from __future__ import annotations

import base64
import hashlib
import hmac as hmac_lib
import time
from typing import Any, Callable, Protocol
from urllib.parse import quote, urlparse, urlunparse

import httpx

from atlas.security.egress import EgressGuard

IM_CHANNELS = ("dingtalk", "wecom", "feishu")
IM_TIMEOUT_SECONDS = 10.0
MAX_MENTION_ITEMS = 20


class ImDeliveryError(Exception):
    """IM 投递失败（网络/超时/非 2xx/平台错误码/非 JSON/未知渠道/非法消息参数）。"""


def normalize_mentions(value: Any) -> dict[str, Any]:
    """归一化 mentions 为 {userIds:[...], mobiles:[...], atAll:bool}。

    元素去空白、去重保序；userIds/mobiles 各 ≤ MAX_MENTION_ITEMS。非法输入抛 ValueError
    （service 层折算 INVALID_PARAMETER，fail-fast 不投递）。
    """
    if value is None:
        return {"userIds": [], "mobiles": [], "atAll": False}
    if not isinstance(value, dict):
        raise ValueError("mentions 必须是对象")
    extra = set(value.keys()) - {"userIds", "mobiles", "atAll"}
    if extra:
        raise ValueError(f"mentions 含未知字段：{sorted(extra)}")

    def _norm_list(key: str) -> list[str]:
        raw = value.get(key, [])
        if raw is None:
            return []
        if not isinstance(raw, (list, tuple)):
            raise ValueError(f"mentions.{key} 必须是数组")
        items: list[str] = []
        seen: set[str] = set()
        for element in raw:
            if not isinstance(element, str):
                raise ValueError(f"mentions.{key} 元素必须是字符串")
            stripped = element.strip()
            if not stripped or stripped in seen:
                continue
            seen.add(stripped)
            items.append(stripped)
        if len(items) > MAX_MENTION_ITEMS:
            raise ValueError(f"mentions.{key} 至多 {MAX_MENTION_ITEMS} 个")
        return items

    at_all = value.get("atAll", False)
    if not isinstance(at_all, bool):
        raise ValueError("mentions.atAll 必须是布尔值")
    return {"userIds": _norm_list("userIds"), "mobiles": _norm_list("mobiles"), "atAll": at_all}


def _at_suffix_dingtalk(user_ids: list[str], mobiles: list[str]) -> str:
    """钉钉 text/markdown 正文末尾必须出现 @手机号/@userId 才有 @效果（docs/58 §3.2）。"""
    tokens = [f"@{m}" for m in mobiles] + [f"@{u}" for u in user_ids]
    return " ".join(tokens)


def _build_dingtalk(subject, body, msg_format, mentions):
    at_block = {
        "atMobiles": mentions["mobiles"],
        "atUserIds": mentions["userIds"],
        "isAtAll": mentions["atAll"],
    }
    if msg_format == "markdown":
        text = body.rstrip()
        if not mentions["atAll"]:
            suffix = _at_suffix_dingtalk(mentions["userIds"], mentions["mobiles"])
            if suffix:
                text = f"{text}\n{suffix}"
        return {"msgtype": "markdown", "markdown": {"title": subject, "text": text}, "at": at_block}
    content = f"{subject}\n{body}".rstrip()
    if not mentions["atAll"]:
        suffix = _at_suffix_dingtalk(mentions["userIds"], mentions["mobiles"])
        if suffix:
            content = f"{content}\n{suffix}"
    return {"msgtype": "text", "text": {"content": content}, "at": at_block}


def _build_wecom(subject, body, msg_format, mentions):
    user_ids, mobiles, at_all = mentions["userIds"], mentions["mobiles"], mentions["atAll"]
    if msg_format == "markdown":
        # markdown 体无 mentioned_* 字段；<@userid> 为官方明文，<@all> 为社区一致用法。
        # markdown 模式 mobiles 无字段可用（官方限制，忽略）。
        content = body.rstrip()
        tags = [f"<@{u}>" for u in user_ids]
        if at_all:
            tags.append("<@all>")
        if tags:
            content = f"{content} {' '.join(tags)}".rstrip()
        return {"msgtype": "markdown", "markdown": {"content": content}}
    mentioned_list = list(user_ids)
    mentioned_mobiles = list(mobiles)
    if at_all:
        mentioned_list.append("@all")
        mentioned_mobiles.append("@all")
    return {
        "msgtype": "text",
        "text": {
            "content": f"{subject}\n{body}".rstrip(),
            "mentioned_list": mentioned_list,
            "mentioned_mobile_list": mentioned_mobiles,
        },
    }


def _build_feishu(subject, body, timestamp, secret, msg_format, mentions):
    user_ids, at_all = mentions["userIds"], mentions["atAll"]
    base: dict[str, Any]
    if msg_format == "markdown":
        # markdown 模式落 post 富文本（post 不渲染 markdown 语法符号，docs/58 §3.2 限制）。
        paragraphs = [[{"tag": "text", "text": line}] for line in body.split("\n") if line.strip()]
        at_tags = [{"tag": "at", "user_id": uid} for uid in user_ids]
        if at_all:
            at_tags.append({"tag": "at", "user_id": "all", "user_name": "所有人"})
        if at_tags:
            paragraphs.append(at_tags)
        base = {
            "msg_type": "post",
            "content": {"post": {"zh_cn": {"title": subject, "content": paragraphs}}},
        }
    else:
        text = f"{subject}\n{body}".rstrip()
        # mobiles 飞书无 @能力，忽略（docs/58 §3.1）；展示名以 id 兜底。
        tags = [f'<at user_id="{uid}">{uid}</at>' for uid in user_ids]
        if at_all:
            tags.append('<at user_id="all">所有人</at>')
        if tags:
            text = f"{text} {' '.join(tags)}".rstrip()
        base = {"msg_type": "text", "content": {"text": text}}
    if secret:
        base["timestamp"] = str(timestamp)
        base["sign"] = _feishu_sign(timestamp, secret)
    return base


def _feishu_sign(timestamp: int, secret: str) -> str:
    digest = hmac_lib.new(b"", f"{timestamp}\n{secret}".encode(), hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def build_payload(
    channel: str,
    subject: str,
    body: str,
    timestamp: int,
    secret: str | None,
    msg_format: str = "text",
    mentions: Any = None,
) -> dict[str, Any]:
    """按渠道构造群机器人消息体（纯函数，不触网；测试逐字节固定向量）。"""
    if msg_format not in ("text", "markdown"):
        raise ImDeliveryError(f"不支持的消息格式：{msg_format}")
    norm = normalize_mentions(mentions)
    if channel == "dingtalk":
        return _build_dingtalk(subject, body, msg_format, norm)
    if channel == "wecom":
        return _build_wecom(subject, body, msg_format, norm)
    if channel == "feishu":
        return _build_feishu(subject, body, timestamp, secret, msg_format, norm)
    raise ImDeliveryError(f"未知 IM 渠道：{channel}")


def sign_dingtalk_url(url: str, timestamp_ms: int, secret: str) -> str:
    """钉钉 URL query 加签：sign=quote(base64(HMAC_SHA256(key=secret, f"{ms}\\n{secret}")))。"""
    string_to_sign = f"{timestamp_ms}\n{secret}"
    digest = hmac_lib.new(secret.encode(), string_to_sign.encode(), hashlib.sha256).digest()
    sign = quote(base64.b64encode(digest).decode())  # 已 quote，直接拼接、不再 urlencode
    parsed = urlparse(url)
    sep = "&" if parsed.query else ""
    new_query = f"{parsed.query}{sep}timestamp={timestamp_ms}&sign={sign}"
    return urlunparse(parsed._replace(query=new_query))


def _check_platform_response(channel: str, data: Any) -> None:
    if not isinstance(data, dict):
        raise ImDeliveryError("IM 目标返回非 JSON 对象")
    if channel in ("dingtalk", "wecom"):
        if data.get("errcode") != 0:
            raise ImDeliveryError(f"IM 目标平台错误码：{data.get('errcode')}（{data.get('errmsg')}）")
    elif channel == "feishu":
        if data.get("code") != 0:
            raise ImDeliveryError(f"IM 目标平台错误码：{data.get('code')}（{data.get('msg')}）")


class ImSender(Protocol):
    def send(
        self,
        channel: str,
        url: str,
        subject: str,
        body: str,
        secret: str | None = None,
        msg_format: str = "text",
        mentions: Any = None,
    ) -> None: ...


class DefaultImSender:
    """默认 IM 群机器人投递器；guard/post/clock 均可注入（测试不触网）。"""

    def __init__(
        self,
        guard: EgressGuard | None = None,
        post: Callable[..., httpx.Response] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._guard = guard if guard is not None else EgressGuard.from_env()
        self._post = post if post is not None else self._default_post
        self._clock = clock if clock is not None else time.time

    @staticmethod
    def _default_post(url: str, **kwargs: Any) -> httpx.Response:
        # follow_redirects=False：禁止重定向把已校验目标带到内网（SSRF 纵深防御）。
        return httpx.post(url, follow_redirects=False, **kwargs)

    def send(
        self,
        channel: str,
        url: str,
        subject: str,
        body: str,
        secret: str | None = None,
        msg_format: str = "text",
        mentions: Any = None,
    ) -> None:
        if channel not in IM_CHANNELS:
            raise ImDeliveryError(f"未知 IM 渠道：{channel}")
        now = int(self._clock())
        # 1) SSRF 出向校验：EgressDenied 直接向上抛（保持其 code）。
        self._guard.check(url)
        # 2) 钉钉加签体现在 URL query。
        target_url = url
        if channel == "dingtalk" and secret:
            target_url = sign_dingtalk_url(url, now * 1000, secret)
        # 3) 构造消息体（非法 msg_format/mentions 在 build_payload 内抛 ImDeliveryError）。
        payload = build_payload(channel, subject, body, now, secret, msg_format, mentions)
        # 4) 投递。
        try:
            response = self._post(
                target_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=IM_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as exc:
            raise ImDeliveryError(f"IM 请求失败：{exc}") from exc
        status = getattr(response, "status_code", None)
        if not isinstance(status, int) or not 200 <= status < 300:
            raise ImDeliveryError(f"IM 目标返回非 2xx 状态码：{status}")
        try:
            data = response.json()
        except ValueError as exc:
            raise ImDeliveryError("IM 目标返回非 JSON") from exc
        _check_platform_response(channel, data)


_im_sender: DefaultImSender | None = None


def get_im_sender() -> DefaultImSender:
    """进程级惰性单例。"""
    global _im_sender
    if _im_sender is None:
        _im_sender = DefaultImSender()
    return _im_sender
