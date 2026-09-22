"""导入 OpenAPI 规格生成的 Harness 适配器（docs/42 §1 B）。

adapter_id = openapi:{spec_id}，adapter_type = api；每个成功导入的
operation 一个 Capability。执行时按描述符渲染 path/query/header/body，
传输复用 HttpApiClient（出向过 EgressGuard），任何 HTTP 响应均 SUCCESS。
"""

from __future__ import annotations

import re
from urllib.parse import quote, urlencode

from atlas.harness.base import (
    ActionRequest,
    ActionResult,
    Capability,
    HarnessAdapter,
    Observation,
    Permission,
    StructuredError,
)
from atlas.httpapi.service import DEFAULT_TIMEOUT, HttpApiCallError, HttpApiClient
from .models import OperationDescriptor
from .store import ImportedSpec

_TEMPLATE_RE = re.compile(r"\{([^{}]+)\}")

_HTTP_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "integer"},
        "headers": {"type": "object"},
        "body": {},
    },
    "required": ["status", "headers", "body"],
}


class ImportedApiHarnessAdapter(HarnessAdapter):
    adapter_type = "api"

    def __init__(
        self,
        spec: ImportedSpec,
        client: HttpApiClient | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.spec = spec
        self.adapter_id = f"openapi:{spec.spec_id}"
        self.client = client or HttpApiClient(base_url=spec.base_url)

    def list_capabilities(self) -> list[Capability]:
        return [self._capability(op) for op in self.spec.operations]

    def _capability(self, op: OperationDescriptor) -> Capability:
        return Capability(
            name=op.name,
            description=op.description or f"{op.method.upper()} {op.path}",
            action=f"{self.adapter_id}/{op.name}",
            input_schema=op.input_schema,
            output_schema=_HTTP_OUTPUT_SCHEMA,
            permission=Permission(op.permission),
            timeout=DEFAULT_TIMEOUT,
            is_idempotent=op.idempotent,
        )

    def _execute(self, request: ActionRequest) -> ActionResult:
        descriptor = next(
            (op for op in self.spec.operations if op.name == request.capability_name),
            None,
        )
        if descriptor is None:
            return ActionResult.failed(
                StructuredError(
                    "UNKNOWN_CAPABILITY", f"未知能力：{request.capability_name}"
                )
            )

        params = request.parameters
        try:
            url = self._build_url(descriptor, params)
            headers = self._headers(descriptor, params)
            output = self.client.request(
                method=descriptor.method,
                url=url,
                headers=headers or None,
                body=params.get("body"),
                timeout=request.timeout or DEFAULT_TIMEOUT,
                idempotent=descriptor.idempotent,
            )
        except HttpApiCallError as exc:
            return ActionResult.failed(StructuredError(exc.code, str(exc)))
        return ActionResult.success(output)

    def _build_url(self, descriptor: OperationDescriptor, params: dict) -> str:
        def replace(match: re.Match[str]) -> str:
            name = match.group(1)
            value = params.get(name)
            if value is None:
                raise HttpApiCallError(
                    "OPENAPI_INVALID_PARAMETER", f"缺少路径参数：{name}"
                )
            return quote(str(value), safe="")

        rendered = _TEMPLATE_RE.sub(replace, descriptor.path)
        query: list[tuple[str, object]] = []
        for name, location in descriptor.locations.items():
            if location != "query":
                continue
            value = params.get(name)
            if value is None:
                continue
            if isinstance(value, list):
                query.extend((name, item) for item in value)
            else:
                query.append((name, value))
        if query:
            rendered = f"{rendered}?{urlencode(query, doseq=True)}"
        return rendered

    def _headers(
        self, descriptor: OperationDescriptor, params: dict
    ) -> dict[str, str]:
        headers: dict[str, str] = {}
        for name, location in descriptor.locations.items():
            if location != "header":
                continue
            value = params.get(name)
            if value is None:
                continue
            headers[name] = str(value)
        return headers

    def observe(self) -> Observation:
        return Observation(
            url=self.spec.base_url,
            title=f"导入 API 适配器：{self.spec.title}",
            data={
                "spec_id": self.spec.spec_id,
                "base_url": self.spec.base_url,
                "last_request": self.client.last_request,
            },
        )
