"""webhook 消息渠道真实投递（docs/35 §3，T3；docs/14 D24 webhook 子集；docs/58 出站签名）。

channel=webhook：向**单个** http(s) URL POST JSON。所有目标先过 SSRF 出向校验
（security.egress，私网/环回/链路本地/元数据地址/非 http(s) 一律拦截），再用 httpx
投递（不跟随重定向，防重定向绕过 SSRF；10s 超时）。

- 出向校验失败抛 EgressDenied（code=EGRESS_DENIED/EGRESS_INVALID_URL），由 MessageService 透传；
- 网络错误/超时/非 2xx 抛 WebhookDeliveryError，由 MessageService 折算 WEBHOOK_SEND_FAILED；
- 投递失败不写消息记录（非幂等写能力，失败须显式）。

docs/58 起支持出站 HMAC-SHA256 签名（secret 非空时）：body 采用确定性紧凑 UTF-8 序列化，
附 X-Atlas-Timestamp（UTC 秒）与 X-Atlas-Signature: sha256=<hex>，签名基串 f"{ts}\\n{raw}"。
无 secret 不发签名头（向后兼容）。

v1 不做限流队列、入站消费、IM/短信（仍缓做 D24）；重试退避在 MessageService 层（docs/56）。
"""

from __future__ import annotations

import hashlib
import hmac as hmac_lib
import json
import time
from typing import Any, Callable, Protocol

import httpx

from atlas.security.egress import EgressGuard

WEBHOOK_TIMEOUT_SECONDS = 10.0
TIMESTAMP_HEADER = "X-Atlas-Timestamp"
SIGNATURE_HEADER = "X-Atlas-Signature"


class WebhookDeliveryError(Exception):
    """webhook 已通过出向校验，但投递失败（网络/超时/非 2xx）。"""


def serialize_payload(payload: dict[str, Any]) -> bytes:
    """确定性紧凑 UTF-8 序列化：签名覆盖字节必须与发送字节完全一致（docs/58 §2.1）。"""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def sign_body(secret: str, timestamp: str, raw: bytes) -> str:
    """HMAC-SHA256 over f"{timestamp}\\n{raw}"，返 hex（GitHub webhook 通行形态）。"""
    base = f"{timestamp}\n".encode("utf-8") + raw
    return hmac_lib.new(secret.encode("utf-8"), base, hashlib.sha256).hexdigest()


def _default_post(url: str, **kwargs: Any) -> httpx.Response:
    # follow_redirects=False：禁止重定向把已校验的目标带到内网地址（SSRF 纵深防御）。
    return httpx.post(url, follow_redirects=False, **kwargs)


class WebhookSender(Protocol):
    def send(self, url: str, payload: dict[str, Any], secret: str | None = None) -> None: ...


class DefaultWebhookSender:
    """默认 webhook 投递器；guard/post/clock 均可注入（测试不触网）。"""

    def __init__(
        self,
        guard: EgressGuard | None = None,
        post: Callable[..., httpx.Response] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._guard = guard if guard is not None else EgressGuard.from_env()
        self._post = post if post is not None else _default_post
        self._clock = clock if clock is not None else time.time

    def send(self, url: str, payload: dict[str, Any], secret: str | None = None) -> None:
        # 1) SSRF 出向校验：EgressDenied 直接向上抛（保持其 code），不在此包装。
        self._guard.check(url)
        # 2) 确定性序列化（签名覆盖字节 == 发送字节）。
        raw = serialize_payload(payload)
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if secret:
            timestamp = str(int(self._clock()))
            headers[TIMESTAMP_HEADER] = timestamp
            headers[SIGNATURE_HEADER] = f"sha256={sign_body(secret, timestamp, raw)}"
        # 3) 投递（content=raw 字节，不再用 httpx json= 隐式序列化）。
        try:
            response = self._post(
                url,
                content=raw,
                headers=headers,
                timeout=WEBHOOK_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as exc:  # 含 TimeoutException / TransportError / RequestError
            raise WebhookDeliveryError(f"webhook 请求失败：{exc}") from exc
        status = getattr(response, "status_code", None)
        if not isinstance(status, int) or not 200 <= status < 300:
            raise WebhookDeliveryError(f"webhook 目标返回非 2xx 状态码：{status}")


_webhook_sender: DefaultWebhookSender | None = None


def get_webhook_sender() -> DefaultWebhookSender:
    """进程级惰性单例（与 get_smtp_sender 同构）。"""
    global _webhook_sender
    if _webhook_sender is None:
        _webhook_sender = DefaultWebhookSender()
    return _webhook_sender
