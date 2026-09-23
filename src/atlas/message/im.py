"""IM 群机器人消息投递（docs/51；docs/14 D24 IM 子集）。

channel=dingtalk / wecom / feishu：向群机器人 Webhook **单个** http(s) URL POST 文本消息。
所有目标先过 SSRF 出向校验（security.egress），再用 httpx 投递（不跟随重定向，10s 超时）。
钉钉/飞书在 robot 安全设置启用加签时按各自算法签名（时间戳取注入时钟，回放可确定）。

三家平台即使 HTTP 200 也可能在响应体携带错误码：
- dingtalk / wecom：errcode == 0 才成功；
- feishu：code == 0 才成功；
非 2xx、坏 JSON 或平台码非 0 抛 ImDeliveryError，由 MessageService 折算 IM_SEND_FAILED。

v1 仅 text 消息；不做富文本/卡片/@人、多 URL 群发、IM 应用 OAuth、入站消费、限流退避。
"""

from __future__ import annotations

import base64
import hashlib
import hmac as hmac_lib
import time
from typing import Any, Callable, Protocol
from urllib.parse import quote

import httpx

from atlas.security.egress import EgressGuard

IM_TIMEOUT_SECONDS = 10.0
IM_CHANNELS = ("dingtalk", "wecom", "feishu")


class ImDeliveryError(Exception):
    """IM 已通过出向校验，但投递失败（网络/超时/非 2xx/平台错误码非 0）。"""


def _default_post(url: str, **kwargs: Any) -> httpx.Response:
    # follow_redirects=False：禁止重定向把已校验目标带到内网地址（SSRF 纵深防御）。
    return httpx.post(url, follow_redirects=False, **kwargs)


class ImSender(Protocol):
    def send(self, channel: str, url: str, text: str, secret: str | None) -> None: ...


def build_payload(channel: str, text: str, timestamp: int, secret: str | None) -> dict[str, Any]:
    """构造各平台请求体（timestamp：dingtalk 为 ms、feishu 为秒，均取同一时间基准）。"""
    if channel == "dingtalk" or channel == "wecom":
        return {"msgtype": "text", "text": {"content": text}}
    # feishu
    payload: dict[str, Any] = {"msg_type": "text", "content": {"text": text}}
    if secret is not None:
        string_to_sign = f"{timestamp}\n{secret}"
        sign = base64.b64encode(
            hmac_lib.new(b"", string_to_sign.encode("utf-8"), hashlib.sha256).digest()
        ).decode("utf-8")
        payload["timestamp"] = str(timestamp)
        payload["sign"] = sign
    return payload


def sign_dingtalk_url(url: str, timestamp_ms: int, secret: str) -> str:
    string_to_sign = f"{timestamp_ms}\n{secret}"
    digest = hmac_lib.new(
        secret.encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha256
    ).digest()
    sign = quote(base64.b64encode(digest).decode("utf-8"))
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}timestamp={timestamp_ms}&sign={sign}"


def _check_platform_response(channel: str, body: Any) -> None:
    if not isinstance(body, dict):
        raise ImDeliveryError(f"{channel} 返回了无法解析的响应")
    if channel == "feishu":
        code = body.get("code")
        ok = code == 0
    else:
        code = body.get("errcode")
        ok = code == 0
    if not ok:
        message = body.get("errmsg") or body.get("msg") or "未知错误"
        raise ImDeliveryError(f"{channel} 返回平台错误码 {code}：{message}")


class DefaultImSender:
    """默认 IM 投递器；guard/post/clock 均可注入（测试不触网）。"""

    def __init__(
        self,
        guard: EgressGuard | None = None,
        post: Callable[..., httpx.Response] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._guard = guard if guard is not None else EgressGuard.from_env()
        self._post = post if post is not None else _default_post
        self._clock = clock if clock is not None else time.time

    def send(self, channel: str, url: str, text: str, secret: str | None) -> None:
        now = int(self._clock())
        target_url = url
        if channel == "dingtalk" and secret is not None:
            target_url = sign_dingtalk_url(url, now * 1000, secret)
        payload = build_payload(channel, text, now, secret)
        # 1) SSRF 出向校验：EgressDenied 直接向上抛（保持其 code），不在此包装。
        self._guard.check(target_url)
        # 2) 投递。
        try:
            response = self._post(
                target_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=IM_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as exc:  # 含 TimeoutException / TransportError / RequestError
            raise ImDeliveryError(f"{channel} 请求失败：{exc}") from exc
        status = getattr(response, "status_code", None)
        if not isinstance(status, int) or not 200 <= status < 300:
            raise ImDeliveryError(f"{channel} 目标返回非 2xx 状态码：{status}")
        # 3) 平台错误码（三家均可能在 HTTP 200 体内携带失败）。
        try:
            body = response.json()
        except ValueError as exc:
            raise ImDeliveryError(f"{channel} 返回了非 JSON 响应") from exc
        _check_platform_response(channel, body)


_im_sender: DefaultImSender | None = None


def get_im_sender() -> DefaultImSender:
    """进程级惰性单例（与 get_webhook_sender 同构）。"""
    global _im_sender
    if _im_sender is None:
        _im_sender = DefaultImSender()
    return _im_sender
