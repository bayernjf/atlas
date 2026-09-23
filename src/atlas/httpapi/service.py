"""通用 HTTP API 客户端（04 §4.6 权威契约 / 06 §6.6 运行时）。

进程内同步 httpx 调用，运行于 graph 线程池工作线程（与 wait/human_approval
同为阻塞模型）。任何拿到 HTTP 响应的结果都由调用方按 SUCCESS 处理（含
4xx/5xx），仅传输层异常转 StructuredError。

出向安全准入（docs/32）：请求在拼出最终绝对 URL 后、建连前先过
:class:`EgressGuard` 的 SSRF 校验（私网/元数据恒拦、可选白名单），拒绝
折叠为 EGRESS_DENIED/EGRESS_INVALID_URL，不产生网络请求、不重试、不计熔断；
客户端不跨 host 跟随重定向（follow_redirects=False）。
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from urllib.parse import urljoin, urlsplit

import httpx

from atlas.security.egress import EgressDenied, EgressGuard
from atlas.security.secrets import (
    SecretError,
    build_secret_provider_from_env,
    redact_headers,
    resolve_references,
)

from .resilience import (
    DEFAULT_BASE_DELAY,
    DEFAULT_COOLDOWN,
    DEFAULT_FAIL_THRESHOLD,
    DEFAULT_MAX_ATTEMPTS,
    CircuitBreaker,
    CircuitOpenError,
    RetryPolicy,
)

ALLOWED_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
DEFAULT_TIMEOUT = 30.0


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


class HttpApiCallError(Exception):
    """参数校验/传输失败，code 对应 StructuredError.code。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class HttpApiClient:
    def __init__(
        self,
        base_url: str = "",
        default_headers: dict[str, str] | None = None,
        client: httpx.Client | None = None,
        *,
        egress: EgressGuard | None = None,
        resolver: Callable[[str], list[str]] | None = None,
        retry: RetryPolicy | None = None,
        breaker: CircuitBreaker | None = None,
        secret_provider=None,
    ) -> None:
        self.base_url = (base_url or "").strip()
        self.default_headers: dict[str, str] = {
            str(k): str(v) for k, v in (default_headers or {}).items()
        }
        self._client = client
        # egress 恒启用；resolver 仅供离线测试注入（生产用系统 DNS）
        self._egress = egress or (EgressGuard(resolver=resolver) if resolver else EgressGuard())
        # 重试/熔断恒启用；可注入 no-op 睡眠策略与假时钟供离线测试
        self._retry = retry or RetryPolicy()
        self._breaker = breaker or CircuitBreaker()
        self._secrets = secret_provider
        self.last_request: dict[str, object] | None = None

    @classmethod
    def from_env(cls) -> "HttpApiClient":
        """从环境变量读默认连接配置：

        ATLAS_HTTPAPI_BASE_URL 默认 base URL；
        ATLAS_HTTPAPI_HEADERS JSON 对象，公共请求头；
        ATLAS_HTTPAPI_TOKEN 注入 Authorization: Bearer（不覆盖显式同名头）。
        """
        headers: dict[str, str] = {}
        raw = os.getenv("ATLAS_HTTPAPI_HEADERS", "").strip()
        if raw:
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError("ATLAS_HTTPAPI_HEADERS 不是合法 JSON 对象") from exc
            if not isinstance(parsed, dict):
                raise ValueError("ATLAS_HTTPAPI_HEADERS 必须是 JSON 对象")
            headers = {str(k): str(v) for k, v in parsed.items()}
        token = os.getenv("ATLAS_HTTPAPI_TOKEN", "").strip()
        if token:
            headers.setdefault("Authorization", f"Bearer {token}")
        retry = RetryPolicy(
            max_attempts=_int_env("ATLAS_HTTP_MAX_ATTEMPTS", DEFAULT_MAX_ATTEMPTS),
            base_delay=_float_env("ATLAS_HTTP_RETRY_BASE_DELAY", DEFAULT_BASE_DELAY),
        )
        breaker = CircuitBreaker(
            fail_threshold=_int_env("ATLAS_HTTP_CIRCUIT_FAIL_THRESHOLD", DEFAULT_FAIL_THRESHOLD),
            cooldown=_float_env("ATLAS_HTTP_CIRCUIT_COOLDOWN", DEFAULT_COOLDOWN),
        )
        return cls(
            base_url=os.getenv("ATLAS_HTTPAPI_BASE_URL", ""),
            default_headers=headers,
            egress=EgressGuard.from_env(),
            retry=retry,
            breaker=breaker,
            secret_provider=build_secret_provider_from_env(),
        )

    def request(
        self,
        method: str = "GET",
        url: str = "",
        headers: dict[str, object] | None = None,
        body: object = None,
        timeout: float = DEFAULT_TIMEOUT,
        idempotent: bool = False,
        auth: object = None,
    ) -> dict[str, object]:
        method = (method or "GET").upper()
        if method not in ALLOWED_METHODS:
            raise HttpApiCallError("INVALID_PARAMETER", f"不支持的 HTTP 方法：{method}")
        if not isinstance(url, str) or not url.strip():
            raise HttpApiCallError("MISSING_PARAMETER", "缺少 url")
        if headers is not None and not isinstance(headers, dict):
            raise HttpApiCallError("INVALID_PARAMETER", "headers 必须是 JSON 对象")
        try:
            timeout_value = float(timeout)
        except (TypeError, ValueError) as exc:
            raise HttpApiCallError("INVALID_PARAMETER", "timeout 必须是正数秒") from exc
        if timeout_value <= 0:
            raise HttpApiCallError("INVALID_PARAMETER", "timeout 必须是正数秒")
        if body is not None and not isinstance(body, (dict, list, str)):
            raise HttpApiCallError("INVALID_PARAMETER", "body 必须是 JSON 对象/数组或字符串")

        merged_headers = {
            **self.default_headers,
            **{str(k): str(v) for k, v in (headers or {}).items()},
        }
        # 凭证解析：secret:// 引用与 enc$ 信封在拼 URL/建连前替换为明文；
        # 缺失或解密失败 fail-closed（不写 last_request、零外呼、不进重试/熔断）
        try:
            resolved_url = resolve_references(url.strip(), self._secrets)
            resolved_headers = {
                str(key): str(resolve_references(value, self._secrets))
                for key, value in merged_headers.items()
            }
        except SecretError as exc:
            raise HttpApiCallError(exc.code, str(exc)) from exc
        target = self._resolve_url(resolved_url)
        # SSRF 出向校验：在拼出最终绝对 URL 后、建连前；拒绝零外呼、不写 last_request
        try:
            self._egress.check(target)
        except EgressDenied as exc:
            raise HttpApiCallError(exc.code, str(exc)) from exc

        self.last_request = {"method": method, "url": target}
        host = urlsplit(target).hostname or ""
        try:
            self._breaker.before_call(host)  # 开闸则请求不发出，快速失败
        except CircuitOpenError as exc:
            raise HttpApiCallError(exc.code, str(exc)) from exc

        def _once() -> httpx.Response:
            return self._get_client().request(
                method,
                target,
                content=body if isinstance(body, str) else None,
                json=body if isinstance(body, (dict, list)) else None,
                headers=resolved_headers,
                timeout=timeout_value,
                auth=auth,
            )

        try:
            response = self._retry.execute(_once, method=method, idempotent=idempotent)
        except httpx.TransportError as exc:
            self._breaker.record_failure(host)
            if isinstance(exc, httpx.TimeoutException):
                raise HttpApiCallError("HTTP_TIMEOUT", f"HTTP 请求超时：{exc}") from exc
            raise HttpApiCallError("HTTP_CONNECT_ERROR", f"HTTP 请求失败：{exc}") from exc

        # 仅临时网关状态计熔断；拿到任何其他响应（含 4xx/500）视为对端在响应
        if self._retry.is_transient_status(response.status_code):
            self._breaker.record_failure(host)
        else:
            self._breaker.record_success(host)
        self.last_request["status"] = response.status_code
        try:
            parsed_body: object = response.json()
        except ValueError:
            parsed_body = response.text
        return {
            "status": response.status_code,
            "headers": redact_headers(dict(response.headers)),
            "body": parsed_body,
        }

    def _resolve_url(self, url: str) -> str:
        if "://" in url:
            return url
        if not self.base_url:
            raise HttpApiCallError(
                "MISSING_PARAMETER", "url 为相对路径但未配置 ATLAS_HTTPAPI_BASE_URL"
            )
        return urljoin(self.base_url.rstrip("/") + "/", url.lstrip("/"))

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            # 不跨 host 自动跟随重定向，避免 302 跳内网绕过 egress 校验（docs/32 §3）
            self._client = httpx.Client(follow_redirects=False)
        return self._client
