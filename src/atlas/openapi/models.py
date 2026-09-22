from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Permission = Literal["read", "write"]

HTTP_METHODS = ("get", "post", "put", "patch", "delete", "head", "options")


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
    skipped: bool = False
    skip_reason: str | None = None


class ParsedSpec(BaseModel):
    title: str
    version: str = ""
    base_url: str
    operations: list[OperationDescriptor]
