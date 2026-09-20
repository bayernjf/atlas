"""进程内记忆存储（docs/26 §4.2）：MemoryStore 实现 MemoryRepository。

per-tenant 实例（由 TenantRegistry 装配，同其他 store）；持 list[dict] +
threading.Lock + 租户级 ``mem-N`` 计数器（reset 归零）。embedding 随条目存内存，
对外返回时剔除（``_public``）。PG 实现见 storage/pg.py PgMemoryStore（批 3）。
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any

from atlas.memory.embeddings import EmbeddingProvider, cosine_similarity, get_embedding_provider
from atlas.memory.models import MemoryItem, validate_recall_params, validate_remember_params

# 对外字段（不含 embedding），顺序即列表/响应字段顺序。
_PUBLIC_FIELDS = (
    "id",
    "kind",
    "content",
    "scope",
    "confidence",
    "source",
    "metadata",
    "created_at",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def public_memory(item: dict[str, Any]) -> dict[str, Any]:
    """剔除内部 embedding 字段的对外形态（两档共用，docs/26 §2.1）。"""
    return {key: item[key] for key in _PUBLIC_FIELDS}


def scope_contains(item_scope: dict[str, str], required: dict[str, str]) -> bool:
    """子集匹配：item.scope 须包含 required 的全部键值（docs/26 §4.1）。"""
    return all(item_scope.get(key) == value for key, value in required.items())


class MemoryStore:
    """进程内长期记忆（fact/preference），重启清空、reset 清空。"""

    def __init__(self, provider: EmbeddingProvider | None = None) -> None:
        self._items: list[dict[str, Any]] = []
        self._counter = 0
        self._lock = threading.Lock()
        self._provider = provider or get_embedding_provider()

    def remember(
        self,
        *,
        kind: str,
        content: str,
        scope: dict[str, str] | None = None,
        confidence: float = 1.0,
        source: str = "tool",
        metadata: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        params = validate_remember_params(
            kind=kind,
            content=content,
            scope=scope,
            confidence=confidence,
            source=source,
            metadata=metadata,
        )
        vector = self._provider.embed([params["content"]])[0]
        with self._lock:
            self._counter += 1
            item = MemoryItem(
                id=f"mem-{self._counter}",
                kind=params["kind"],
                content=params["content"],
                scope=params["scope"],
                confidence=params["confidence"],
                source=params["source"],
                metadata=params["metadata"],
                embedding=vector,
                created_at=_utc_now(),
            )
            record = item.model_dump()
            self._items.append(record)
        return public_memory(record)

    def recall(
        self,
        query: str,
        *,
        kind: str | None = None,
        scope: dict[str, str] | None = None,
        top_k: int = 5,
        min_score: float = 0.0,
    ) -> list[dict[str, Any]]:
        params = validate_recall_params(
            query=query, kind=kind, scope=scope, top_k=top_k, min_score=min_score
        )
        query_vector = self._provider.embed([params["query"]])[0]
        with self._lock:
            candidates = list(self._items)
        scored: list[tuple[float, dict[str, Any]]] = []
        for record in candidates:
            if params["kind"] is not None and record["kind"] != params["kind"]:
                continue
            if not scope_contains(record["scope"], params["scope"]):
                continue
            score = cosine_similarity(query_vector, record["embedding"])
            if score < params["min_score"]:
                continue
            scored.append((score, record))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        results: list[dict[str, Any]] = []
        for score, record in scored[: params["top_k"]]:
            public = public_memory(record)
            public["score"] = round(score, 6)
            results.append(public)
        return results

    def list(self, *, kind: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """倒序（新→旧）；kind 可选过滤。"""
        if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
            raise ValueError("limit 必须是正整数")
        with self._lock:
            items = list(self._items)
        if kind is not None:
            if kind not in ("fact", "preference"):
                raise ValueError("kind 必须是 fact 或 preference")
            items = [item for item in items if item["kind"] == kind]
        return [public_memory(item) for item in reversed(items[-limit:])]

    def delete(self, memory_id: str) -> bool:
        """按 id 删除；不存在返回 False（实例天然只含本租户）。"""
        with self._lock:
            for index, item in enumerate(self._items):
                if item["id"] == memory_id:
                    del self._items[index]
                    return True
        return False

    def clear(self) -> None:
        """reset 用（RESET_RESETTABLE，与 graph 同）。"""
        with self._lock:
            self._items = []
            self._counter = 0
