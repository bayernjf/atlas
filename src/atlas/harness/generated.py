# -*- coding: utf-8 -*-
"""NL 造工具采纳后生成的 Harness 适配器（docs/133 打包 BQ＝D71；docs/127 里程碑 F）。

adapter_id = ``generated:{draft_id}``，adapter_type = ``api``；每条采纳草稿的
capability 一个 Capability（name/description/method/path/input·output schema/
permission/is_idempotent——AdapterDefinition 的 HTTP 描述符）。执行时按
method/path 模板渲染并走 HttpApiClient（出向过 EgressGuard），照
``ImportedApiHarnessAdapter`` 同款纪律（任何 HTTP 响应均 SUCCESS，传输层失败 FAILED）。

**F-P2 最小权限**：基类 ``HarnessAdapter`` 缺省 granted_permissions={READ}；本适配器
不额外开权限——采纳动作本身即 admin 人审（端点层），执行权限按草稿/授予集校验。
"""

from __future__ import annotations

import re
from typing import Any
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


class GeneratedApiHarnessAdapter(HarnessAdapter):
    adapter_type = "api"

    def __init__(
        self,
        draft: dict[str, Any],
        client: HttpApiClient | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.draft = draft
        self.definition = draft.get("definition") or {}
        self.adapter_id = f"generated:{draft['draft_id']}"
        self.client = client or HttpApiClient(base_url=self.definition.get("base_url", ""))

    def list_capabilities(self) -> list[Capability]:
        return [self._capability(cap) for cap in self.definition.get("capabilities") or []]

    def _capability(self, cap: dict[str, Any]) -> Capability:
        return Capability(
            name=cap["name"],
            description=cap.get("description") or f"{cap.get('method', 'GET')} {cap.get('path', '/')}",
            action=f"{self.adapter_id}/{cap['name']}",
            input_schema=cap.get("input_schema") or {},
            output_schema=cap.get("output_schema") or _HTTP_OUTPUT_SCHEMA,
            permission=Permission(cap.get("permission", "read")),
            timeout=DEFAULT_TIMEOUT,
            is_idempotent=bool(cap.get("is_idempotent", False)),
        )

    def _execute(self, request: ActionRequest) -> ActionResult:
        descriptor = next(
            (cap for cap in self.definition.get("capabilities") or []
             if cap.get("name") == request.capability_name),
            None,
        )
        if descriptor is None:
            return ActionResult.failed(
                StructuredError(
                    "UNKNOWN_CAPABILITY", f"未知能力：{request.capability_name}"
                )
            )
        params = request.parameters or {}
        try:
            url = self._build_url(descriptor, params)
            headers = self._headers(descriptor, params)
            output = self.client.request(
                method=descriptor.get("method", "GET"),
                url=url,
                headers=headers or None,
                body=params.get("body"),
                timeout=request.timeout or DEFAULT_TIMEOUT,
                idempotent=bool(descriptor.get("is_idempotent", False)),
            )
        except HttpApiCallError as exc:
            return ActionResult.failed(StructuredError(exc.code, str(exc)))
        return ActionResult.success(output)

    def _build_url(self, descriptor: dict[str, Any], params: dict) -> str:
        def replace(match: re.Match[str]) -> str:
            name = match.group(1)
            value = params.get(name)
            if value is None:
                raise HttpApiCallError(
                    "GENERATED_INVALID_PARAMETER", f"缺少路径参数：{name}"
                )
            return quote(str(value), safe="")

        rendered = _TEMPLATE_RE.sub(replace, descriptor.get("path", "/"))
        query: list[tuple[str, object]] = []
        schema = descriptor.get("input_schema") or {}
        for name in (schema.get("properties") or {}):
            if name in params and name != "body":
                value = params[name]
                if isinstance(value, list):
                    query.extend((name, item) for item in value)
                else:
                    query.append((name, value))
        if query:
            rendered = f"{rendered}?{urlencode(query, doseq=True)}"
        return rendered

    def _headers(self, descriptor: dict[str, Any], params: dict) -> dict[str, str]:
        headers: dict[str, str] = {}
        body = params.get("body")
        if isinstance(body, dict):
            for key, value in body.items():
                if isinstance(value, (str, int, float, bool)):
                    headers[str(key)] = str(value)
        return headers

    def observe(self) -> Observation:
        return Observation(
            url=self.definition.get("base_url", ""),
            title=f"生成适配器：{self.definition.get('name', '')}",
            data={
                "draft_id": self.draft["draft_id"],
                "base_url": self.definition.get("base_url", ""),
                "last_request": self.client.last_request,
            },
        )
