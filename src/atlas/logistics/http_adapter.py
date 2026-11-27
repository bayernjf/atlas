"""本地/内网可达的物流 HTTP 适配器（T3）。

与 `atlas/logistics/adapter.py` 的进程内假适配器同契约（`check_receipt` /
`sign_receipt`），但把签收状态放到一个真实 HTTP 服务上：

- 基址只来自 `ATLAS_LOGISTICS_URL`（或显式构造参数），不接收图里的任意 URL，
  因此不把通用 HTTP 节点的 SSRF 面暴露给物流适配器；
- `GET  {base}/receipts/{order_id}` → `{"order_id": "...", "signed": true|false}`；
- `POST {base}/receipts/{order_id}/sign` → 同上；
- 传输/非 2xx/坏 JSON 都折成 `ActionResult.failed(StructuredError)`，由 run 的
  节点结果可见，不抛栈。

T3 的证据用本机 `ThreadingHTTPServer` 起这个契约端点，证明是真 HTTP 可达，
而不是进程内假字典；生产接真实物流系统时只需把 `ATLAS_LOGISTICS_URL` 指向它。
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import quote

import httpx

from atlas.harness.base import (
    ActionRequest,
    ActionResult,
    Capability,
    HarnessAdapter,
    Observation,
    Permission,
    StructuredError,
)

_CHECK_RECEIPT_INPUT_SCHEMA = {
    "type": "object",
    "properties": {"order_id": {"type": "string"}},
    "required": ["order_id"],
}

_RECEIPT_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "order_id": {"type": "string"},
        "signed": {"type": "boolean"},
    },
    "required": ["order_id", "signed"],
}


class HttpLogisticsAdapter(HarnessAdapter):
    adapter_id = "logistics"
    adapter_type = "logistics"

    def __init__(
        self,
        base_url: str | None = None,
        *,
        client: httpx.Client | None = None,
        timeout: float = 10.0,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.base_url = (base_url or os.getenv("ATLAS_LOGISTICS_URL") or "").strip().rstrip("/")
        self.timeout = timeout
        self._client = client
        self.last_response: dict[str, Any] | None = None

    def list_capabilities(self) -> list[Capability]:
        return [
            Capability(
                name="check_receipt",
                description="查询退货是否已签收入库（HTTP 物流服务）",
                action="check_receipt",
                input_schema=_CHECK_RECEIPT_INPUT_SCHEMA,
                output_schema=_RECEIPT_OUTPUT_SCHEMA,
                permission=Permission.READ,
                is_idempotent=True,
                timeout=self.timeout,
            ),
            Capability(
                name="sign_receipt",
                description="标记退货已签收入库（HTTP 物流服务）",
                action="sign_receipt",
                input_schema=_CHECK_RECEIPT_INPUT_SCHEMA,
                output_schema=_RECEIPT_OUTPUT_SCHEMA,
                permission=Permission.WRITE,
                timeout=self.timeout,
            ),
        ]

    def _execute(self, request: ActionRequest) -> ActionResult:
        order_id = request.parameters.get("order_id", "")
        if not isinstance(order_id, str) or not order_id.strip():
            return ActionResult.failed(StructuredError("MISSING_PARAMETER", "缺少 order_id"))
        if not self.base_url:
            return ActionResult.failed(
                StructuredError("LOGISTICS_NOT_CONFIGURED", "ATLAS_LOGISTICS_URL 未配置")
            )
        capability = request.capability_name
        if capability == "check_receipt":
            method = "GET"
            path = f"/receipts/{quote(order_id, safe='')}"
        elif capability == "sign_receipt":
            method = "POST"
            path = f"/receipts/{quote(order_id, safe='')}/sign"
        else:
            return ActionResult.failed(StructuredError("UNKNOWN_CAPABILITY", capability))

        url = f"{self.base_url}{path}"
        try:
            response = self._get_client().request(
                method, url, timeout=request.timeout or self.timeout
            )
        except httpx.TimeoutException:
            return ActionResult.failed(StructuredError("HTTP_TIMEOUT", f"物流服务超时：{url}"))
        except httpx.HTTPError as exc:
            return ActionResult.failed(
                StructuredError("HTTP_CONNECT_ERROR", f"物流服务不可达：{exc}")
            )
        self.last_response = {"status": response.status_code, "body": None}
        if response.status_code >= 400:
            return ActionResult.failed(
                StructuredError(
                    "LOGISTICS_HTTP_ERROR",
                    f"物流服务返回 HTTP {response.status_code}",
                    {"status": response.status_code},
                )
            )
        try:
            payload = response.json()
        except ValueError:
            return ActionResult.failed(
                StructuredError(
                    "LOGISTICS_RESPONSE_NOT_JSON",
                    f"物流服务返回非 JSON：HTTP {response.status_code}",
                    {"status": response.status_code},
                )
            )
        self.last_response = {"status": response.status_code, "body": payload}
        if not isinstance(payload, dict) or not isinstance(payload.get("signed"), bool):
            return ActionResult.failed(
                StructuredError(
                    "LOGISTICS_RESPONSE_INVALID",
                    "物流服务返回缺少布尔 signed 字段",
                    {"body": payload},
                )
            )
        return ActionResult.success(
            {
                "order_id": str(payload.get("order_id") or order_id),
                "signed": bool(payload["signed"]),
            }
        )

    def observe(self) -> Observation:
        return Observation(
            url=self.base_url or "http://unconfigured",
            title="物流适配器（HTTP 本地/内网服务）",
            data={"base_url": self.base_url, "last_response": self.last_response},
        )

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(follow_redirects=False)
        return self._client
