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
