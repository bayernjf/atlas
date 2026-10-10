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
from atlas.memory.models import (
    MemoryItem,
    decay_multiplier,
    dedup_match,
    max_version,
    validate_decay,
    validate_dedup_keys,
    validate_recall_params,
    validate_remember_params,
    validate_update_policy,
)

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
        update_policy: str = "overwrite",
        dedup_keys: list[str] | None = None,
    ) -> dict[str, Any]:
        params = validate_remember_params(
            kind=kind,
            content=content,
            scope=scope,
            confidence=confidence,
            source=source,
            metadata=metadata,
        )
        policy = validate_update_policy(update_policy)
        keys = validate_dedup_keys(dedup_keys)
        vector = self._provider.embed([params["content"]])[0]
        with self._lock:
            matched = [
                record
                for record in self._items
                if dedup_match(
                    record["scope"],
                    record["content"],
                    kind=record["kind"],
                    match_kind=params["kind"],
                    scope=params["scope"],
                    content=params["content"],
                    dedup_keys=keys,
                )
            ]
            if matched:
                if policy == "ask":
                    # 不静默：把冲突交回调用方决定（v1 不挂审批，docs/122 D-4）。
                    latest = matched[-1]
                    return {
                        "conflict": True,
                        "existing_id": latest["id"],
                        "existing": public_memory(latest),
                    }
                if policy == "overwrite":
                    target = matched[-1]
                    target.update(
                        content=params["content"],
                        confidence=params["confidence"],
                        metadata=params["metadata"],
                        source=params["source"],
                        embedding=vector,
                    )
                    # created_at 与 id 不变＝"这条事实被更新了"，不是"又记了一遍"。
                    return public_memory(target)
                params["metadata"] = {
                    **params["metadata"],
                    "version": str(max_version(matched) + 1),
                }
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
        category: str | None = None,
        scope: dict[str, str] | None = None,
        top_k: int = 5,
        min_score: float = 0.0,
        decay: dict[str, Any] | None = None,
        now_override: datetime | None = None,
    ) -> list[dict[str, Any]]:
        params = validate_recall_params(
            query=query, kind=kind, category=category, scope=scope, top_k=top_k, min_score=min_score
        )
        decay_rule = validate_decay(decay)
        query_vector = self._provider.embed([params["query"]])[0]
        with self._lock:
            candidates = list(self._items)
        scored: list[tuple[float, dict[str, Any]]] = []
        for record in candidates:
            if params["kind"] is not None and record["kind"] != params["kind"]:
                continue
            if (
                params["category"] is not None
                and record.get("metadata", {}).get("category") != params["category"]
            ):
                continue
            if not scope_contains(record["scope"], params["scope"]):
                continue
            score = cosine_similarity(query_vector, record["embedding"])
            if score < params["min_score"]:
                continue
            scored.append((score, record))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        results: list[dict[str, Any]] = []
        if decay_rule is not None:
            # 衰减只对**过了 min_score 的候选集**做，且在这里统一重排（两档同一公式同一处）。
            half_life = decay_rule["half_life_days"]
            now = now_override or datetime.now(timezone.utc)
            scored = [
                (score * decay_multiplier(record.get("created_at"), now, half_life), record)
                for score, record in scored
            ]
            scored.sort(key=lambda pair: pair[0], reverse=True)
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
            if kind not in ("fact", "preference", "knowledge"):
                raise ValueError("kind 必须是 fact/preference/knowledge 之一")
            items = [item for item in items if item["kind"] == kind]
        return [public_memory(item) for item in reversed(items[-limit:])]

    def update(self, memory_id: str, **fields: Any) -> dict[str, Any] | None:
        """手动编辑白名单字段（docs/28 §5.1）；不存在返回 None，source 强制 manual。

        合并后整体过 validate_remember_params；content 变化重算 embedding，
        id/created_at 不变。与 PG 档共享 memory.models.merge_manual_update。
        """
        from atlas.memory.models import merge_manual_update

        with self._lock:
            target = next(
                (item for item in self._items if item["id"] == memory_id), None
            )
            if target is None:
                return None
            params, content_changed = merge_manual_update(public_memory(target), fields)
            vector = target["embedding"]
            if content_changed:
                vector = self._provider.embed([params["content"]])[0]
            target.update(
                {
                    "kind": params["kind"],
                    "content": params["content"],
                    "scope": params["scope"],
                    "confidence": params["confidence"],
                    "source": "manual",
                    "metadata": params["metadata"],
                    "embedding": vector,
                }
            )
            return public_memory(dict(target))

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
