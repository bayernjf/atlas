from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Permission = Literal["read", "write"]

HTTP_METHODS = ("get", "post", "put", "patch", "delete", "head", "options")


class SecurityScheme(BaseModel):
    name: str
    kind: Literal["api_key", "bearer", "basic", "digest"]
    location: Literal["header", "query"] | None
    param: str
    prefix: str = ""


class OperationDescriptor(BaseModel):
    name: str
    method: str
    path: str
    summary: str = ""
    description: str = ""
    permission: Permission = "write"
    idempotent: bool = False
    input_schema: dict = Field(default_factory=dict)
    locations: dict[str, str] = Field(default_factory=dict)
    security: list[list[str]] = Field(default_factory=list)
    skipped: bool = False
    skip_reason: str | None = None


class ParsedSpec(BaseModel):
    title: str
    version: str = ""
    base_url: str
    operations: list[OperationDescriptor]
    security_schemes: dict[str, SecurityScheme] = Field(default_factory=dict)

    def content_fingerprint(self) -> str:
        """docs/56 §3.1：对解析后稳定子集算 sha256，用于同内容导入去重。

        只取 title/base_url、安全方案的键与类型、每个 operation 的 method/path/
        name/skipped（顺序以文档为准，同文档稳定）；不含导入时间或生成的 spec_id，
        同一文档重复解析必得同一指纹，改 base_url 或任一 operation 即变。
        """
        import hashlib
        import json

        payload = {
            "title": self.title,
            "base_url": self.base_url,
            "security": {
                key: {
                    "kind": scheme.kind,
                    "location": scheme.location,
                    "param": scheme.param,
                    "prefix": scheme.prefix,
                }
                for key, scheme in self.security_schemes.items()
            },
            "operations": [
                {
                    "method": op.method,
                    "path": op.path,
                    "name": op.name,
                    "skipped": op.skipped,
                }
                for op in self.operations
            ],
        }
        blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()
