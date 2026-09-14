"""通用 HTTP API 客户端（04 §4.6 权威契约 / 06 §6.6 运行时）。

进程内同步 httpx 调用，运行于 graph 线程池工作线程（与 wait/human_approval
同为阻塞模型）。任何拿到 HTTP 响应的结果都由调用方按 SUCCESS 处理（含
4xx/5xx），仅传输层异常转 StructuredError。
"""

from __future__ import annotations

import json
import os
from urllib.parse import urljoin

import httpx

ALLOWED_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
DEFAULT_TIMEOUT = 30.0


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
    ) -> None:
        self.base_url = (base_url or "").strip()
        self.default_headers: dict[str, str] = {
            str(k): str(v) for k, v in (default_headers or {}).items()
        }
        self._client = client
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
        return cls(base_url=os.getenv("ATLAS_HTTPAPI_BASE_URL", ""), default_headers=headers)

    def request(
        self,
        method: str = "GET",
        url: str = "",
        headers: dict[str, object] | None = None,
        body: object = None,
        timeout: float = DEFAULT_TIMEOUT,
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
        target = self._resolve_url(url.strip())

        self.last_request = {"method": method, "url": target}
        try:
            response = self._get_client().request(
                method,
                target,
                content=body if isinstance(body, str) else None,
                json=body if isinstance(body, (dict, list)) else None,
                headers=merged_headers,
                timeout=timeout_value,
            )
        except httpx.TimeoutException as exc:
            raise HttpApiCallError("HTTP_TIMEOUT", f"HTTP 请求超时：{exc}") from exc
        except httpx.RequestError as exc:
            raise HttpApiCallError("HTTP_CONNECT_ERROR", f"HTTP 请求失败：{exc}") from exc

        self.last_request["status"] = response.status_code
        try:
            parsed_body: object = response.json()
        except ValueError:
            parsed_body = response.text
        return {
            "status": response.status_code,
            "headers": dict(response.headers),
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
            self._client = httpx.Client()
        return self._client
