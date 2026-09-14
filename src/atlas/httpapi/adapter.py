"""通用 HTTP API Harness 适配器（adapter_id="http", adapter_type="api"）。

单能力 http/request（write，非幂等）；参数契约见 04 §4.6 权威 blockquote，
运行时语义见 06 §6.6。
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
from .service import DEFAULT_TIMEOUT, HttpApiCallError, HttpApiClient

_REQUEST_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"]},
        "url": {"type": "string", "description": "绝对 URL 或相对 baseUrl 的路径"},
        "headers": {"type": "object", "additionalProperties": {"type": "string"}},
        "body": {"description": "对象/数组按 JSON 发送，字符串原样发送"},
        "timeout": {"type": "number", "minimum": 0, "exclusiveMinimum": True},
    },
    "required": ["url"],
}

_REQUEST_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "integer"},
        "headers": {"type": "object"},
        "body": {},
    },
    "required": ["status", "headers", "body"],
}


class HttpApiHarnessAdapter(HarnessAdapter):
    adapter_id = "http"
    adapter_type = "api"

    def __init__(self, client: HttpApiClient | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.client = client or HttpApiClient.from_env()

    def list_capabilities(self) -> list[Capability]:
        return [
            Capability(
                name="request",
                description="发起通用 HTTP 请求（任何 HTTP 响应均为成功，按 status 判分支）",
                action="http_request",
                input_schema=_REQUEST_INPUT_SCHEMA,
                output_schema=_REQUEST_OUTPUT_SCHEMA,
                permission=Permission.WRITE,
                timeout=DEFAULT_TIMEOUT,
            ),
        ]

    def _execute(self, request: ActionRequest) -> ActionResult:
        params = request.parameters
        try:
            output = self.client.request(
                method=params.get("method", "GET"),
                url=params.get("url", ""),
                headers=params.get("headers"),
                body=params.get("body"),
                timeout=params.get("timeout", request.timeout or DEFAULT_TIMEOUT),
            )
        except HttpApiCallError as exc:
            return ActionResult.failed(StructuredError(exc.code, str(exc)))
        return ActionResult.success(output)

    def observe(self) -> Observation:
        return Observation(
            url=self.client.base_url or "http://unconfigured",
            title="API 适配器（通用 HTTP）",
            data={"base_url": self.client.base_url, "last_request": self.client.last_request},
        )
