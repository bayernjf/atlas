"""长期记忆 Harness 适配器（adapter_id="memory", adapter_type="memory"，docs/26 §5）。

三能力（零新节点 / 零 DSL 改动，走现有 tool_call/harness，params 由 M3 FormRenderer
按 input_schema 自动生成）：
- ``remember``（write，非幂等）：写 fact/preference/knowledge（knowledge 带 meta.category 子类）；
- ``recall``（read，幂等）：语义检索，无命中返空结果（成功，不报错）；
- ``auto_extract``（write，**非幂等**，打包 AK docs/122 D-2）：LLM 从文本里提炼事实/偏好并按
  置信度门槛落库；没接模型时返回 `skipped="llm_unavailable"` 而不是让 run 失败——
  记忆是增强项，但"没接线"必须被说出来而不是伪装成"没提取到"。
  非幂等的原因：去重按内容精确匹配，而模型两次措辞不会完全一致（真机实测见 docs/122 §6）。

repo 缺省（全局发现注册实例）时执行期不可用；执行期由 ``_runtime_registry`` 注入
当前租户的 MemoryRepository（照 message 两段式，docs/26 §5.1）。
"""

from __future__ import annotations

import logging
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
from atlas.llm.structured import LLMStructuredUnavailable, extract_memories
from atlas.memory.models import MemoryValidationError

logger = logging.getLogger(__name__)

# docs/26 §1.2 的 0.85 是缺省门槛、不是真理：真实语料校准前它就是一个可调参数。
_DEFAULT_EXTRACT_MIN_CONFIDENCE = 0.85

_REMEMBER_INPUT_SCHEMA = {
    "type": "object",
    "required": ["kind", "content"],
    "properties": {
        "kind": {
            "type": "string",
            "enum": ["fact", "preference", "knowledge"],
            "description": "记忆类型：事实 fact / 用户偏好 preference / 知识 knowledge（知识条目的 meta.category 用 faq/sop/manual/rule/case）",
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
        # 打包 AK（docs/122 D-4）：缺省 overwrite＝与"同 scope 同内容不堆叠"的旧语义兼容。
        "update_policy": {
            "type": "string",
            "enum": ["overwrite", "version", "ask"],
            "default": "overwrite",
            "description": (
                "命中既有条目时怎么办：overwrite 原地更新（id/created_at 不变）、"
                "version 新建一条并把 meta.version 递增、ask 不写库只返回冲突交调用方决定"
            ),
        },
        "dedup_keys": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "去重键＝scope 的键名（如 [\"object_id\"]）；缺省空＝按同 scope＋同内容去重"
            ),
        },
    },
}

_REMEMBER_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["id", "kind", "content", "created_at"],
    "properties": {
        "id": {"type": "string"},
        "kind": {"type": "string", "enum": ["fact", "preference", "knowledge"]},
        "content": {"type": "string"},
        "scope": {"type": "object", "additionalProperties": {"type": "string"}},
        "confidence": {"type": "number"},
        "source": {"type": "string"},
        "metadata": {"type": "object", "additionalProperties": {"type": "string"}},
        "created_at": {"type": "string"},
        # update_policy=ask 且命中时返回的是 {conflict, existing_id, existing} 三键形状
        # （不写库），required 那三项此时不存在——调用方先看 conflict 再取条目字段。
        "conflict": {"type": "boolean"},
        "existing_id": {"type": "string"},
        "existing": {"type": "object"},
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
        "kind": {"type": "string", "enum": ["fact", "preference", "knowledge"], "description": "仅检索该类型（知识条目选 knowledge）"},
        "category": {"type": "string", "enum": ["faq", "sop", "manual", "rule", "case"], "description": "知识子类过滤（仅 kind=knowledge 时生效）：FAQ/SOP/手册/规则/案例"},
        "scope": {
            "type": "object",
            "additionalProperties": {"type": "string"},
            "description": "仅在该作用域内检索（子集匹配）",
        },
        "top_k": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
        "min_score": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.0},
        # 打包 AK（docs/122 D-5）：缺省不传＝纯 cosine，与接入前逐字一致。
        "decay": {
            "type": "object",
            "properties": {
                "enabled": {"type": "boolean", "default": False},
                "half_life_days": {
                    "type": "integer", "minimum": 1, "maximum": 3650, "default": 30,
                },
            },
            "description": (
                "时间衰减：score' = score × 0.5^(age_days/half_life_days)，按衰减后的分数重排。"
                "不传或 enabled=false 时排序与接入前逐字一致"
            ),
        },
    },
}

_AUTO_EXTRACT_INPUT_SCHEMA = {
    "type": "object",
    "required": ["text"],
    "properties": {
        "text": {
            "type": "string",
            "minLength": 1,
            "maxLength": 4000,
            "description": "要从中提炼事实/偏好的文本（支持 {{变量}} 插值，如客服对话）",
        },
        "min_confidence": {
            "type": "number",
            "minimum": 0,
            "maximum": 1,
            "default": 0.85,
            "description": "置信度门槛（docs/26 §1.2 的 0.85 是缺省值，不是真理；按业务校准）",
        },
        "scope": {
            "type": "object",
            "additionalProperties": {"type": "string"},
            "description": "提炼出的条目挂到哪个业务作用域，如 {user_id}；缺省不挂",
        },
    },
}

_AUTO_EXTRACT_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["extracted", "facts", "preferences", "min_confidence"],
    "properties": {
        "extracted": {"type": "integer"},
        "facts": {"type": "array"},
        "preferences": {"type": "array"},
        "dropped_below_threshold": {"type": "integer"},
        "min_confidence": {"type": "number"},
        # "没接模型"与"这次没提取到"必须是两个答案（docs/122 复核块第 2 条）：
        # 前者 skipped=llm_unavailable，后者 skipped 为空而 extracted=0。
        "skipped": {"type": "string"},
        "reason": {"type": "string"},
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
                    "kind": {"type": "string", "enum": ["fact", "preference", "knowledge"]},
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
            Capability(
                name="auto_extract",
                description=(
                    "用 LLM 从一段文本（客服对话、工单、备注）里提炼可长期保留的事实与偏好并写库；"
                    "低于置信度门槛的丢弃，没接模型时在产出里明说而不是静默返回空"
                ),
                action="memory_auto_extract",
                input_schema=_AUTO_EXTRACT_INPUT_SCHEMA,
                output_schema=_AUTO_EXTRACT_OUTPUT_SCHEMA,
                permission=Permission.WRITE,
                timeout=30.0,
                # 真机验证（2026-10-11，docs/122 §6）：同一段文本重跑两次，模型两次措辞不同
                # → 精确内容去重不命中 → 记忆从 3 条变 6 条。"重跑幂等"是假承诺，故显式标非幂等。
                is_idempotent=False,
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
                    update_policy=params.get("update_policy"),
                    dedup_keys=params.get("dedup_keys"),
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
                    category=params.get("category"),
                    scope=params.get("scope"),
                    top_k=top_k,
                    min_score=min_score,
                    decay=params.get("decay"),
                )
                return ActionResult.success({"results": results})
            if request.capability_name == "auto_extract":
                return self._auto_extract(params)
        except MemoryValidationError as exc:
            return ActionResult.failed(StructuredError("MEMORY_INVALID_INPUT", str(exc)))
        except Exception as exc:  # noqa: BLE001 - 存储故障统一折 STORAGE_ERROR，不炸运行
            return ActionResult.failed(StructuredError("STORAGE_ERROR", f"记忆存储故障：{exc}"))
        return ActionResult.failed(
            StructuredError("UNKNOWN_CAPABILITY", f"未知能力：{request.capability_name}")
        )

    def _auto_extract(self, params: dict[str, Any]) -> ActionResult:
        """LLM 提炼 → 置信度门槛 → 落库（docs/122 D-2）。

        落库走缺省 `overwrite`，去重按**同 scope 同内容精确匹配**：模型两次给出的措辞只要
        差一个字就会新增一条，所以"对同一段对话重跑一次"**不保证条数不变**（2026-10-11 真机
        实测 3 条 → 6 条）。语义近似去重缓做，触发条件见 docs/14 D65。
        """
        text = str(params.get("text") or "").strip()
        if not text:
            return ActionResult.failed(StructuredError("MEMORY_INVALID_INPUT", "text 不能为空"))
        raw_threshold = params.get("min_confidence", _DEFAULT_EXTRACT_MIN_CONFIDENCE)
        if (
            isinstance(raw_threshold, bool)
            or not isinstance(raw_threshold, (int, float))
            or not 0.0 <= float(raw_threshold) <= 1.0
        ):
            return ActionResult.failed(
                StructuredError("MEMORY_INVALID_INPUT", "min_confidence 必须是 0-1 的数")
            )
        threshold = float(raw_threshold)
        scope = params.get("scope") if isinstance(params.get("scope"), dict) else None
        try:
            extracted = extract_memories(text=text)
        except LLMStructuredUnavailable as exc:
            # 不炸 run（记忆是增强项），但把原因写进产出——"没接线"不等于"没提取到"。
            return ActionResult.success({
                "extracted": 0, "facts": [], "preferences": [], "dropped_below_threshold": 0,
                "min_confidence": threshold, "skipped": "llm_unavailable", "reason": str(exc),
            })
        kept: dict[str, list[dict[str, Any]]] = {"fact": [], "preference": []}
        dropped = 0
        for kind, candidates in (
            ("fact", extracted.get("facts") or []),
            ("preference", extracted.get("preferences") or []),
        ):
            for candidate in candidates:
                if float(candidate["confidence"]) < threshold:
                    dropped += 1
                    continue
                saved = self.repo.remember(
                    kind=kind,
                    content=candidate["content"],
                    scope=scope,
                    confidence=candidate["confidence"],
                    source="auto_extract",
                )
                kept[kind].append({
                    "id": saved.get("id"),
                    "content": saved.get("content"),
                    "confidence": candidate["confidence"],
                })
        if dropped:
            logger.info("auto_extract 丢弃 %d 条低于门槛 %.2f 的候选", dropped, threshold)
        return ActionResult.success({
            "extracted": len(kept["fact"]) + len(kept["preference"]),
            "facts": kept["fact"],
            "preferences": kept["preference"],
            "dropped_below_threshold": dropped,
            "min_confidence": threshold,
            "skipped": "",
        })
