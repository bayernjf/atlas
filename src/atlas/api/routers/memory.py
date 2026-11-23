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
from atlas.memory.documents import (
    DocumentParseError,
    decode_base64_document,
    detect_format,
    document_digest,
    extract_text,
    normalize_filename,
)
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
    return {"results": [_with_citation(item) for item in results]}


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


def _with_citation(item: dict[str, Any]) -> dict[str, Any]:
    """知识召回附加可追溯引用（A-08）：source/doc_id/chunk_index 来自导入元数据。"""
    meta = item.get("metadata") or {}
    if item.get("kind") != "knowledge" or not meta.get("source"):
        return item
    citation: dict[str, Any] = {
        "source": meta["source"],
        "format": meta.get("format", ""),
        "doc_id": meta.get("doc_id", ""),
        "chunk_index": meta.get("chunk_index", ""),
        "chunk_count": meta.get("chunk_count", ""),
    }
    for key in ("chunk_index", "chunk_count"):
        try:
            citation[key] = int(citation[key])
        except (TypeError, ValueError):
            pass
    return {**item, "citation": citation}


def _knowledge_text_and_meta(
    body: KnowledgeImportRequest,
) -> tuple[str, dict[str, str]]:
    """把 text 或 base64 文件统一成待分段文本 + 引用元数据（A-08）。"""
    has_text = body.text is not None
    has_file = body.content_base64 is not None
    if has_text == has_file:
        raise HTTPException(status_code=422, detail="text 与 content_base64 必须二选一")
    if has_file:
        if not body.filename:
            raise HTTPException(status_code=422, detail="文件导入必须提供 filename")
        try:
            raw, name, fmt = decode_base64_document(body.content_base64 or "", body.filename)
            text = extract_text(raw, fmt)
        except DocumentParseError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return text, {
            "source": name,
            "format": fmt,
            "doc_id": document_digest(raw),
        }
    assert body.text is not None
    if not body.filename:
        return body.text, {}
    try:
        name = normalize_filename(body.filename)
        fmt = detect_format(name)
    except DocumentParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if fmt == "docx":
        raise HTTPException(
            status_code=422, detail="text 导入不支持 .docx；请用 content_base64 上传文件"
        )
    return body.text, {
        "source": name,
        "format": fmt,
        "doc_id": document_digest(body.text.encode("utf-8")),
    }


@router.post("/api/knowledge/import", status_code=201)
def knowledge_import(
    body: KnowledgeImportRequest,
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    """知识库文档导入（operate；docs/108 §2.2 / §7 A-08）。

    ``text`` 或 ``content_base64`` 二选一；文件按 ``filename`` 后缀解析
    .md/.markdown/.txt/.docx。返回 ``{imported, truncated, items}``；超
    ``_KNOWLEDGE_IMPORT_LIMIT`` 条截断并置 ``truncated: true``。失败 422 中文。
    """
    text, source_meta = _knowledge_text_and_meta(body)
    segments = _split_knowledge_segments(text)
    if not segments:
        raise HTTPException(status_code=422, detail="导入内容没有可用的有效段落")
    total_segments = len(segments)
    truncated = total_segments > _KNOWLEDGE_IMPORT_LIMIT
    if truncated:
        segments = segments[:_KNOWLEDGE_IMPORT_LIMIT]
    repo = _main.services_for(principal).memory_store
    items: list[dict[str, Any]] = []
    try:
        for index, segment in enumerate(segments):
            metadata = {"category": body.category, **source_meta}
            if source_meta:
                metadata["chunk_index"] = str(index)
                metadata["chunk_count"] = str(total_segments)
            item = repo.remember(
                kind="knowledge",
                content=segment,
                scope=body.scope,
                confidence=1.0,
                source="manual",
                metadata=metadata,
            )
            items.append(item)
    except MemoryValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"imported": len(items), "truncated": truncated, "items": items}
