"""长期记忆 Harness 适配器（adapter_id="memory", adapter_type="memory"，docs/26 §5）。

两能力（零新节点 / 零 DSL 改动，走现有 tool_call/harness，params 由 M3 FormRenderer
按 input_schema 自动生成）：
- ``remember``（write，非幂等）：写 fact/preference；
- ``recall``（read，幂等）：语义检索，无命中返空结果（成功，不报错）。

repo 缺省（全局发现注册实例）时执行期不可用；执行期由 ``_runtime_registry`` 注入
当前租户的 MemoryRepository（照 message 两段式，docs/26 §5.1）。
"""

from __future__ import annotations

from typing import Any

from atlas.harness.base import (
    ActionRequest,
    ActionResult,
    Capability,
    HarnessAdapter,
    Observation,
    Permission,
    StructuredError,
)
from atlas.memory.models import MemoryValidationError

_REMEMBER_INPUT_SCHEMA = {
    "type": "object",
    "required": ["kind", "content"],
    "properties": {
        "kind": {
            "type": "string",
            "enum": ["fact", "preference"],
            "description": "记忆类型：事实 fact / 用户偏好 preference",
        },
        "content": {
            "type": "string",
            "minLength": 1,
            "maxLength": 2000,
            "description": "要记住的内容（支持 {{变量}} 插值）",
        },
        "scope": {
            "type": "object",
            "additionalProperties": {"type": "string"},
            "description": "业务绑定，如 {user_id, order_id}；recall 时按子集匹配",
        },
        "confidence": {
            "type": "number",
            "minimum": 0,
            "maximum": 1,
            "description": "置信度，缺省 1.0",
        },
        "metadata": {
            "type": "object",
            "additionalProperties": {"type": "string"},
            "description": "附加信息（可选）",
        },
    },
}

_REMEMBER_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["id", "kind", "content", "created_at"],
    "properties": {
        "id": {"type": "string"},
        "kind": {"type": "string", "enum": ["fact", "preference"]},
        "content": {"type": "string"},
        "scope": {"type": "object", "additionalProperties": {"type": "string"}},
        "confidence": {"type": "number"},
        "source": {"type": "string"},
        "metadata": {"type": "object", "additionalProperties": {"type": "string"}},
        "created_at": {"type": "string"},
    },
}

_RECALL_INPUT_SCHEMA = {
    "type": "object",
    "required": ["query"],
    "properties": {
        "query": {
            "type": "string",
            "minLength": 1,
            "maxLength": 2000,
            "description": "检索语义的自然语言/关键词（支持 {{变量}} 插值）",
        },
        "kind": {"type": "string", "enum": ["fact", "preference"], "description": "仅检索该类型"},
        "scope": {
            "type": "object",
            "additionalProperties": {"type": "string"},
            "description": "仅在该作用域内检索（子集匹配）",
        },
        "top_k": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
        "min_score": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.0},
    },
}

_RECALL_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["results"],
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "content", "score"],
                "properties": {
                    "id": {"type": "string"},
                    "kind": {"type": "string", "enum": ["fact", "preference"]},
                    "content": {"type": "string"},
                    "score": {"type": "number"},
                    "confidence": {"type": "number"},
                    "scope": {"type": "object", "additionalProperties": {"type": "string"}},
                    "source": {"type": "string"},
                    "created_at": {"type": "string"},
                },
            },
        }
    },
}


class MemoryHarnessAdapter(HarnessAdapter):
    adapter_id = "memory"
    adapter_type = "memory"

    def __init__(self, repo: Any | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.repo = repo

    def list_capabilities(self) -> list[Capability]:
        return [
            Capability(
                name="remember",
                description="记住一条长期事实（fact）或用户偏好（preference），供后续运行语义检索",
                action="memory_remember",
                input_schema=_REMEMBER_INPUT_SCHEMA,
                output_schema=_REMEMBER_OUTPUT_SCHEMA,
                permission=Permission.WRITE,
                timeout=10.0,
                is_idempotent=False,
            ),
            Capability(
                name="recall",
                description="按自然语言/关键词语义检索本租户长期记忆，返回最相关的若干条（无命中返空）",
                action="memory_recall",
                input_schema=_RECALL_INPUT_SCHEMA,
                output_schema=_RECALL_OUTPUT_SCHEMA,
                permission=Permission.READ,
                timeout=10.0,
                is_idempotent=True,
            ),
        ]

    def observe(self) -> Observation:
        count = None
        if self.repo is not None:
            count = len(self.repo.list(limit=200))
        return Observation(
            url="obs://memory/in-process",
            title="长期记忆适配器（fact/preference）",
            data={"available": self.repo is not None, "count": count},
        )

    def _execute(self, request: ActionRequest) -> ActionResult:
        if self.repo is None:
            return ActionResult.failed(
                StructuredError("MEMORY_NOT_CONFIGURED", "记忆存储未在当前租户装配")
            )
        params = request.parameters
        try:
            if request.capability_name == "remember":
                confidence = params.get("confidence", 1.0)
                if confidence is None:
                    confidence = 1.0
                item = self.repo.remember(
                    kind=params.get("kind"),
                    content=params.get("content"),
                    scope=params.get("scope"),
                    confidence=confidence,
                    source="tool",
                    metadata=params.get("metadata"),
                )
                return ActionResult.success(item)
            if request.capability_name == "recall":
                top_k = params.get("top_k", 5)
                if top_k is None:
                    top_k = 5
                min_score = params.get("min_score", 0.0)
                if min_score is None:
                    min_score = 0.0
                results = self.repo.recall(
                    params.get("query"),
                    kind=params.get("kind"),
                    scope=params.get("scope"),
                    top_k=top_k,
                    min_score=min_score,
                )
                return ActionResult.success({"results": results})
        except MemoryValidationError as exc:
            return ActionResult.failed(StructuredError("MEMORY_INVALID_INPUT", str(exc)))
        except Exception as exc:  # noqa: BLE001 - 存储故障统一折 STORAGE_ERROR，不炸运行
            return ActionResult.failed(StructuredError("STORAGE_ERROR", f"记忆存储故障：{exc}"))
        return ActionResult.failed(
            StructuredError("UNKNOWN_CAPABILITY", f"未知能力：{request.capability_name}")
        )
