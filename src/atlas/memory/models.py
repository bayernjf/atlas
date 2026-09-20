"""M11 长期记忆领域模型与入参校验（docs/26 §2）。

v1 仅 fact / preference 两类（统一 MemoryItem、``kind`` 区分，不建两张表）；
working 由 run outputs/globals 承担，summary/case 缓做 docs/14 D35。

- 资源字段对外 snake_case（与 feedback / demo messages / monitoring 同口径）；
- ``embedding`` 是内部字段，不进 API 响应（``model_dump(exclude=True)``）；
- 入参校验在本层统一做，进程内 / PG 两档共享（docs/26 §4.1）。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, ConfigDict

MemoryKind = Literal["fact", "preference"]
MemorySource = Literal["tool", "manual", "run"]

CONTENT_MIN_LENGTH = 1
CONTENT_MAX_LENGTH = 2000
TOP_K_MIN = 1
TOP_K_MAX = 20
LIST_LIMIT_MAX = 200
SCOPE_DEPTH_MAX = 1  # scope 为扁平 string→string 绑定


class MemoryValidationError(ValueError):
    """记忆入参非法；适配器层折 MEMORY_INVALID_INPUT，REST 折 422。"""


class MemoryItem(BaseModel):
    """统一记忆条目（进程内为 dict、PG 为一行，字段一致，docs/26 §2.1）。"""

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: MemoryKind
    content: str = Field(min_length=CONTENT_MIN_LENGTH, max_length=CONTENT_MAX_LENGTH)
    scope: dict[str, str] = Field(default_factory=dict)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source: MemorySource = "tool"
    metadata: dict[str, str] = Field(default_factory=dict)
    # 内部字段：content 的向量（EMBED_DIM 维）；出口经 public_dict 剔除，不进 API 响应。
    embedding: list[float] = Field(default_factory=list)
    created_at: str

    def public_dict(self) -> dict[str, Any]:
        """对外形态：剔除 embedding（docs/26 §2.1）。"""
        return self.model_dump(exclude={"embedding"})


def _validate_scope(scope: Any, *, field: str = "scope") -> dict[str, str]:
    if scope is None:
        return {}
    if not isinstance(scope, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in scope.items()
    ):
        raise MemoryValidationError(f"{field} 必须是字符串键值对象")
    return dict(scope)


def _validate_metadata(metadata: Any) -> dict[str, str]:
    if metadata is None:
        return {}
    if not isinstance(metadata, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in metadata.items()
    ):
        raise MemoryValidationError("metadata 必须是字符串键值对象")
    return dict(metadata)


def validate_remember_params(
    *,
    kind: Any,
    content: Any,
    scope: Any = None,
    confidence: Any = 1.0,
    source: Any = "tool",
    metadata: Any = None,
) -> dict[str, Any]:
    """校验 remember 入参，返回归一化后的 kwargs（docs/26 §4.1/§5.2）。"""
    if kind not in ("fact", "preference"):
        raise MemoryValidationError("kind 必须是 fact 或 preference")
    if not isinstance(content, str) or not content.strip():
        raise MemoryValidationError("content 必须是非空字符串")
    content = content.strip()
    if len(content) > CONTENT_MAX_LENGTH:
        raise MemoryValidationError(f"content 最长 {CONTENT_MAX_LENGTH} 字")
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
        raise MemoryValidationError("confidence 必须是 0-1 的数")
    confidence_value = float(confidence)
    if not 0.0 <= confidence_value <= 1.0:
        raise MemoryValidationError("confidence 必须在 0-1 之间")
    if source not in ("tool", "manual", "run"):
        raise MemoryValidationError("source 必须是 tool/manual/run")
    return {
        "kind": kind,
        "content": content,
        "scope": _validate_scope(scope),
        "confidence": confidence_value,
        "source": source,
        "metadata": _validate_metadata(metadata),
    }


# docs/28 §5.1（批 4 ⑩，D35 部分取回）：手动新建/编辑允许的白名单字段。
# id/created_at/tenant/embedding 不可改；source 对手动写入强制 "manual"。
REMEMBER_EDITABLE_FIELDS = ("kind", "content", "scope", "confidence", "metadata")


def merge_manual_update(
    old: dict[str, Any], fields: dict[str, Any]
) -> tuple[dict[str, Any], bool]:
    """合并手动编辑白名单字段并整体过校验，source 强制 ``manual``（docs/28 §5.1）。

    ``old`` 为对外形态（含 kind/content/scope/confidence/source/metadata/created_at）；
    ``fields`` 仅允许 :data:`REMEMBER_EDITABLE_FIELDS` 键（REST 层 pydantic extra=forbid
    已挡未知键，此层再防御一次）。返回 ``(validate_remember_params 归一 kwargs,
    content 是否变化)``，两档存储共享以防漂移；content 变化时调用方重算 embedding。
    """
    unknown = sorted(set(fields) - set(REMEMBER_EDITABLE_FIELDS))
    if unknown:
        raise MemoryValidationError(f"不可修改字段：{', '.join(unknown)}")
    merged = {
        key: (fields[key] if key in fields else old.get(key))
        for key in REMEMBER_EDITABLE_FIELDS
    }
    params = validate_remember_params(source="manual", **merged)
    old_content = old.get("content", "")
    if not isinstance(old_content, str):
        old_content = ""
    content_changed = params["content"] != old_content.strip()
    return params, content_changed


def validate_recall_params(
    *,
    query: Any,
    kind: Any = None,
    scope: Any = None,
    top_k: Any = 5,
    min_score: Any = 0.0,
) -> dict[str, Any]:
    """校验 recall 入参，返回归一化后的 kwargs（docs/26 §4.1/§5.2）。"""
    if not isinstance(query, str) or not query.strip():
        raise MemoryValidationError("query 必须是非空字符串")
    query_value = query.strip()
    if kind is not None and kind not in ("fact", "preference"):
        raise MemoryValidationError("kind 必须是 fact 或 preference")
    if not isinstance(top_k, int) or isinstance(top_k, bool):
        # bool 是 int 子类，显式拒绝
        raise MemoryValidationError("top_k 必须是整数")
    if not TOP_K_MIN <= top_k <= TOP_K_MAX:
        raise MemoryValidationError(f"top_k 必须在 {TOP_K_MIN}-{TOP_K_MAX} 之间")
    if not isinstance(min_score, (int, float)) or isinstance(min_score, bool):
        raise MemoryValidationError("min_score 必须是 0-1 的数")
    min_score_value = float(min_score)
    if not 0.0 <= min_score_value <= 1.0:
        raise MemoryValidationError("min_score 必须在 0-1 之间")
    return {
        "query": query_value,
        "kind": kind,
        "scope": _validate_scope(scope),
        "top_k": top_k,
        "min_score": min_score_value,
    }
