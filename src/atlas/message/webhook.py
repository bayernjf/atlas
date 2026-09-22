"""webhook 消息渠道真实投递（docs/35 §3，T3；docs/14 D24 webhook 子集）。

channel=webhook：向**单个** http(s) URL POST JSON。所有目标先过 SSRF 出向校验
（security.egress，私网/环回/链路本地/元数据地址/非 http(s) 一律拦截），再用 httpx
投递（不跟随重定向，防重定向绕过 SSRF；10s 超时）。

- 出向校验失败抛 EgressDenied（code=EGRESS_DENIED/EGRESS_INVALID_URL），由 MessageService 透传；
- 网络错误/超时/非 2xx 抛 WebhookDeliveryError，由 MessageService 折算 WEBHOOK_SEND_FAILED；
- 投递失败不写消息记录（非幂等写能力，失败须显式）。

v1 不做签名、重试退避、限流队列、入站消费、IM/短信（仍缓做 D24）。
"""

from __future__ import annotations

from typing import Any, Callable, Protocol

import httpx

from atlas.security.egress import EgressGuard

WEBHOOK_TIMEOUT_SECONDS = 10.0


class WebhookDeliveryError(Exception):
    """webhook 已通过出向校验，但投递失败（网络/超时/非 2xx）。"""


def _default_post(url: str, **kwargs: Any) -> httpx.Response:
    # follow_redirects=False：禁止重定向把已校验的目标带到内网地址（SSRF 纵深防御）。
    return httpx.post(url, follow_redirects=False, **kwargs)


class WebhookSender(Protocol):
    def send(self, url: str, payload: dict[str, Any]) -> None: ...


class DefaultWebhookSender:
    """默认 webhook 投递器；guard 与 post 均可注入（测试不触网）。"""

    def __init__(
        self,
        guard: EgressGuard | None = None,
        post: Callable[..., httpx.Response] | None = None,
    ) -> None:
        self._guard = guard if guard is not None else EgressGuard.from_env()
        self._post = post if post is not None else _default_post

    def send(self, url: str, payload: dict[str, Any]) -> None:
        # 1) SSRF 出向校验：EgressDenied 直接向上抛（保持其 code），不在此包装。
        self._guard.check(url)
        # 2) 投递（application/json；httpx 用 json= 也会自动设置，显式声明更清晰）。
        try:
            response = self._post(
                url,
                json=payload,
                headers={"Content-Type": "application/json"},
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
