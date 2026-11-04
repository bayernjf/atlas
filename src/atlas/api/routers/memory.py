"""P2-1 split: Long-term memory and knowledge import routes.

Handlers are moved verbatim from atlas/api/main.py; shared helpers and
pydantic models stay in main and are imported back here so the route
surface (method, path, signature, dependencies) is unchanged.
"""

from __future__ import annotations

from fastapi import APIRouter

import atlas.api.main as _main
from atlas.iam.deps import require
from atlas.iam.principals import Principal
from atlas.memory.models import KNOWLEDGE_CATEGORIES, MemoryValidationError
from fastapi import Depends, HTTPException
from typing import Any
from atlas.api.main import (
    KnowledgeImportRequest,
    MemoryCreateRequest,
    MemoryUpdateRequest,
    _KNOWLEDGE_IMPORT_LIMIT,
    _split_knowledge_segments,
)


router: APIRouter = APIRouter()


@router.post("/api/memories", status_code=201)
def create_memory(
    body: MemoryCreateRequest,
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    """手动新建记忆（operate；source 固定 manual，201，docs/28 §5.1）。校验失败 422 中文。"""
    repo = _main.services_for(principal).memory_store
    try:
        return repo.remember(
            kind=body.kind,
            content=body.content,
            scope=body.scope,
            confidence=1.0 if body.confidence is None else body.confidence,
            source="manual",
            metadata=body.metadata,
        )
    except MemoryValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.put("/api/memories/{memory_id}")
def update_memory(
    memory_id: str,
    body: MemoryUpdateRequest,
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    """手动编辑记忆白名单字段（operate；source 置 manual）；空体 422，不存在/他租户 404。"""
    data = body.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(status_code=422, detail="请求体至少包含一个可改字段")
    repo = _main.services_for(principal).memory_store
    try:
        updated = repo.update(memory_id, **data)
    except MemoryValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if updated is None:
        raise HTTPException(status_code=404, detail="记忆不存在")
    return updated


@router.get("/api/memories")
def list_memories(
    kind: str | None = None,
    limit: int = 50,
    principal: Principal = Depends(require("read")),
) -> dict[str, Any]:
    """当前租户记忆倒序列表（不含 embedding）；kind 可选过滤，limit 缺省 50、上限 200。"""
    if kind is not None and kind not in ("fact", "preference", "knowledge"):
        raise HTTPException(status_code=422, detail="kind 必须是 fact/preference/knowledge 之一")
    limit = max(1, min(limit, 200))
    return {"items": _main.services_for(principal).memory_store.list(kind=kind, limit=limit)}


@router.get("/api/memories/search")
def search_memories(
    q: str = "",
    kind: str | None = None,
    category: str | None = None,
    top_k: int = 5,
    min_score: float = 0.0,
    principal: Principal = Depends(require("read")),
) -> dict[str, Any]:
    """语义检索当前租户记忆；q 空白返 422，无命中返空数组（成功不报错）。

    ``category`` 为 docs/108 知识库扩展：仅 kind=knowledge 时参与过滤（非 knowledge
    时由校验层忽略，不误伤既有调用方）。
    """
    if not q or not q.strip():
        raise HTTPException(status_code=422, detail="q 必须是非空检索词")
    if kind is not None and kind not in ("fact", "preference", "knowledge"):
        raise HTTPException(status_code=422, detail="kind 必须是 fact/preference/knowledge 之一")
    if category is not None and category not in KNOWLEDGE_CATEGORIES:
        raise HTTPException(status_code=422, detail="category 必须是 faq/sop/manual/rule/case 之一")
    top_k = max(1, min(top_k, 20))
    min_score = max(0.0, min(min_score, 1.0))
    results = _main.services_for(principal).memory_store.recall(
        q.strip(), kind=kind, category=category, top_k=top_k, min_score=min_score
    )
    return {"results": results}


@router.delete("/api/memories/{memory_id}")
def delete_memory(
    memory_id: str,
    principal: Principal = Depends(require("administer")),
) -> dict[str, bool]:
    """删除一条记忆（admin only）；不存在或跨租户一律 404（不泄漏存在性，T15）。"""
    deleted = _main.services_for(principal).memory_store.delete(memory_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="记忆不存在")
    return {"deleted": True}


@router.post("/api/knowledge/import", status_code=201)
def knowledge_import(
    body: KnowledgeImportRequest,
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    """知识库文档导入（operate；docs/108 §2.2）：纯文本分段向量化入库。

    返回 ``{imported, truncated, items}``；超 ``_KNOWLEDGE_IMPORT_LIMIT`` 条截断并置
    ``truncated: true``（按 docs/108 §2.4，先序段优先）。失败 422 中文（MemoryValidationError
    统一折叠，与 create_memory 同口径）。
    """
    segments = _split_knowledge_segments(body.text)
    if not segments:
        raise HTTPException(status_code=422, detail="text 无可导入的有效内容（全为空段）")
    truncated = len(segments) > _KNOWLEDGE_IMPORT_LIMIT
    if truncated:
        segments = segments[:_KNOWLEDGE_IMPORT_LIMIT]
    repo = _main.services_for(principal).memory_store
    items: list[dict[str, Any]] = []
    try:
        for segment in segments:
            item = repo.remember(
                kind="knowledge",
                content=segment,
                scope=body.scope,
                confidence=1.0,
                source="manual",
                metadata={"category": body.category},
            )
            items.append(item)
    except MemoryValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"imported": len(items), "truncated": truncated, "items": items}
