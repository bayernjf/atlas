# -*- coding: utf-8 -*-
"""网页嵌入配置 store（docs/133 打包 BQ＝D76；docs/127 里程碑 F）。

第七类 SchemaRegistry 实体（F-P1 拍板＝是）：graph 维度"允许只读嵌入"开关，
`enabled` 缺省 false（fail-closed），`token_ttl_seconds` 1–86400（端点层门控）。
内存档为进程内 dict；PG 档见 `PgEmbedStore`（迁移 054 `embed_configs`），
两档方法签名一比一（照 `AutoApplyStore/PgAutoApplyStore` 先例）。
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel

MIN_TOKEN_TTL_SECONDS = 1
MAX_TOKEN_TTL_SECONDS = 86400
DEFAULT_TOKEN_TTL_SECONDS = 3600


class EmbedConfig(BaseModel):
    graph_id: str
    enabled: bool = False
    token_ttl_seconds: int = DEFAULT_TOKEN_TTL_SECONDS
    created_at: str
    updated_at: str


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class EmbedStore:
    """进程内 per-tenant 嵌入配置（随 TenantServices 装配；reset 清本租户）。"""

    def __init__(self) -> None:
        self._configs: dict[str, EmbedConfig] = {}
        self._lock = threading.Lock()

    def get(self, graph_id: str) -> dict[str, Any] | None:
        with self._lock:
            config = self._configs.get(graph_id)
        return config.model_dump() if config is not None else None

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [c.model_dump() for c in self._configs.values()]

    def upsert(
        self,
        graph_id: str,
        *,
        enabled: bool,
        token_ttl_seconds: int,
        now: str | None = None,
    ) -> dict[str, Any]:
        ttl = max(
            MIN_TOKEN_TTL_SECONDS,
            min(int(token_ttl_seconds), MAX_TOKEN_TTL_SECONDS),
        )
        timestamp = now or _now_iso()
        with self._lock:
            existing = self._configs.get(graph_id)
            config = EmbedConfig(
                graph_id=graph_id,
                enabled=bool(enabled),
                token_ttl_seconds=ttl,
                created_at=existing.created_at if existing else timestamp,
                updated_at=timestamp,
            )
            self._configs[graph_id] = config
            return config.model_dump()

    def reset(self) -> None:
        with self._lock:
            self._configs.clear()
