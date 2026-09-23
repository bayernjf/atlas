"""导入 OpenAPI 规格生成的 Harness 适配器（docs/42 §1 B）。

adapter_id = openapi:{spec_id}，adapter_type = api；每个成功导入的
operation 一个 Capability。执行时按描述符渲染 path/query/header/body，
传输复用 HttpApiClient（出向过 EgressGuard），任何 HTTP 响应均 SUCCESS。
"""

from __future__ import annotations

import base64
import json
import re

import httpx
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
from atlas.security.secrets import SecretProvider
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
        secret_provider: SecretProvider | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.spec = spec
        self.adapter_id = f"openapi:{spec.spec_id}"
        self.client = client or HttpApiClient(base_url=spec.base_url)
        self._secret_provider = secret_provider

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
            resolved = self._resolve_credentials(descriptor)
            if isinstance(resolved, StructuredError):
                return ActionResult.failed(resolved)
            extra_headers, extra_query, auth = resolved
            url = self._build_url(descriptor, params, extra_query)
            headers = self._headers(descriptor, params, extra_headers)
            output = self.client.request(
                method=descriptor.method,
                url=url,
                headers=headers or None,
                body=params.get("body"),
                timeout=request.timeout or DEFAULT_TIMEOUT,
                idempotent=descriptor.idempotent,
                auth=auth,
            )
        except HttpApiCallError as exc:
            return ActionResult.failed(StructuredError(exc.code, str(exc)))
        return ActionResult.success(output)

    def _resolve_credentials(
        self, descriptor: OperationDescriptor
    ) -> tuple[dict[str, str], dict[str, str], object] | StructuredError:
        if not descriptor.security:
            return {}, {}, None
        envelopes = self.spec.credential_envelopes
        schemes = self.spec.security_schemes
        missing: list[str] = []
        for group in descriptor.security:
            if not group:
                return {}, {}, None
            absent = [name for name in group if name not in envelopes]
            if absent:
                missing.extend(absent)
                continue
            headers: dict[str, str] = {}
            query: dict[str, str] = {}
            auth: object = None
            for name in group:
                if self._secret_provider is None:
                    return StructuredError(
                        "SECRET_UNAVAILABLE", "密钥提供者不可用，无法解密已配置的密钥"
                    )
                try:
                    value = self._secret_provider.decrypt(envelopes[name])
                except Exception as exc:
                    return StructuredError(
                        "SECRET_DECRYPT_ERROR", f"密钥解密失败：{name}"
                    )
                scheme = schemes[name]
                if scheme.kind in ("basic", "digest"):
                    try:
                        fields = json.loads(value)
                        username = fields["username"]
                        password = fields["password"]
                    except (json.JSONDecodeError, TypeError, KeyError):
                        return StructuredError(
                            "SECRET_DECRYPT_ERROR", f"密钥解密失败：{name}"
                        )
                    if scheme.kind == "basic":
                        token = base64.b64encode(
                            f"{username}:{password}".encode()
                        ).decode("ascii")
                        headers[scheme.param] = f"{scheme.prefix}{token}"
                    else:
                        auth = httpx.DigestAuth(username, password)
                    continue
                rendered = f"{scheme.prefix}{value}"
                if scheme.kind == "api_key" and scheme.location == "query":
                    query[scheme.param] = rendered
                else:
                    headers[scheme.param] = rendered
            return headers, query, auth
        names = "、".join(dict.fromkeys(missing))
        return StructuredError(
            "OPENAPI_CREDENTIAL_MISSING",
            f"该接口需要鉴权但未配置密钥（缺少：{names}）",
        )

    def _build_url(
        self,
        descriptor: OperationDescriptor,
        params: dict,
        extra_query: dict[str, str] | None = None,
    ) -> str:
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
        for name, value in (extra_query or {}).items():
            if not any(pair[0] == name for pair in query):
                query.append((name, value))
        if query:
            rendered = f"{rendered}?{urlencode(query, doseq=True)}"
        return rendered

    def _headers(
        self,
        descriptor: OperationDescriptor,
        params: dict,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, str]:
        headers: dict[str, str] = dict(extra_headers or {})
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
