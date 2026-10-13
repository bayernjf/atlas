"""LLM 用量计量 PG 档（docs/129 打包 AU＝D70；迁移 049 `llm_usage`）。

与进程内 `InMemoryUsageStore` **方法签名一比一**（record/list_recent/summarize/reset），
故 metering helper、TenantServices 装配与 REST 两端点零分叉。形状照
`reflection/pg_store.py`：行内 tenant_id 过滤（非 RLS）、每租户一实例。

两点与内存档的**刻意差异**（docs/129 AU-D2）：
- **不设 ring**：PG 是成本账的权威档，淘汰即丢账；内存档 ring 2000 只是
  Demo/无 PG 形态的有界缓冲（超出覆盖最旧）。
- **不挂 retention**：`ATLAS_RETENTION_DAYS` 本批不接（AU-D7 余部），
  清零只走 `/api/demo/reset`。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import Engine, text

from .metering import UsageRecord

logger = logging.getLogger(__name__)

_COLUMNS = (
    "tenant_id, ts, caller, node_id, graph_id, run_id, model, "
    "prompt_tokens, completion_tokens, total_tokens, latency_ms, status"
)

_GROUP_EXPR = {
    "caller": "caller",
    "model": "model",
    "day": "substr(ts, 1, 10)",
}


class PgUsageStore:
    """`llm_usage` 表 PG 实现（每租户一实例，行内 tenant_id 过滤）。"""

    def __init__(self, engine: Engine, tenant_id: str) -> None:
        self._engine = engine
        self._tenant_id = tenant_id

    def record(self, rec: UsageRecord) -> None:
        with self._engine.begin() as db:
            db.execute(
                text(
                    f"INSERT INTO llm_usage ({_COLUMNS}) VALUES (:tenant_id, :ts, "
                    ":caller, :node_id, :graph_id, :run_id, :model, :prompt_tokens, "
                    ":completion_tokens, :total_tokens, :latency_ms, :status)"
                ),
                {
                    "tenant_id": rec.tenant_id,
                    "ts": rec.ts,
                    "caller": rec.caller,
                    "node_id": rec.node_id,
                    "graph_id": rec.graph_id,
                    "run_id": rec.run_id,
                    "model": rec.model,
                    "prompt_tokens": rec.prompt_tokens,
                    "completion_tokens": rec.completion_tokens,
                    "total_tokens": rec.total_tokens,
                    "latency_ms": rec.latency_ms,
                    "status": rec.status,
                },
            )

    def list_recent(self, limit: int = 50) -> list[dict]:
        bounded = max(1, min(int(limit), 200))
        sql = (
            f"SELECT {_COLUMNS} FROM llm_usage WHERE tenant_id = :t "
            "ORDER BY ts DESC LIMIT :limit"
        )
        with self._engine.connect() as db:
            rows = db.execute(text(sql), {"t": self._tenant_id, "limit": bounded}).all()
        return [_row_view(row) for row in rows]

    def summarize(
        self,
        *,
        since: str | None = None,
        until: str | None = None,
        group_by: str = "caller",
    ) -> list[dict]:
        # group_by 白名单（REST 层已校验；双保险防 SQL 注入进 GROUP BY 表达式）
        expr = _GROUP_EXPR.get(group_by, _GROUP_EXPR["caller"])
        clauses = ["tenant_id = :t"]
        args: dict[str, Any] = {"t": self._tenant_id}
        if since:
            clauses.append("ts >= :since")
            args["since"] = since
        if until:
            clauses.append("ts <= :until")
            args["until"] = until
        sql = (
            f"SELECT {expr} AS key, COUNT(*) AS calls, "
            "COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens, "
            "COALESCE(SUM(completion_tokens), 0) AS completion_tokens, "
            "COALESCE(SUM(total_tokens), 0) AS total_tokens, "
            "COALESCE(SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END), 0) AS errors "
            f"FROM llm_usage WHERE {' AND '.join(clauses)} "
            "GROUP BY key ORDER BY total_tokens DESC"
        )
        with self._engine.connect() as db:
            rows = db.execute(text(sql), args).all()
        return [
            {
                "key": row[0],
                "calls": int(row[1]),
                "promptTokens": int(row[2]),
                "completionTokens": int(row[3]),
                "totalTokens": int(row[4]),
                "errors": int(row[5]),
            }
            for row in rows
        ]

    def reset(self) -> None:
        """/api/demo/reset 清空本租户用量账（运行计量数据，reset 同清）。"""
        with self._engine.begin() as db:
            db.execute(
                text("DELETE FROM llm_usage WHERE tenant_id = :t"),
                {"t": self._tenant_id},
            )


def _row_view(row: Any) -> dict:
    """行 → camelCase 投影（与 InMemoryUsageStore.public_view 逐键一致）。"""
    return {
        "tenantId": row[0],
        "ts": row[1],
        "caller": row[2],
        "nodeId": row[3],
        "graphId": row[4],
        "runId": row[5],
        "model": row[6],
        "promptTokens": int(row[7]),
        "completionTokens": int(row[8]),
        "totalTokens": int(row[9]),
        "latencyMs": round(float(row[10]), 1),
        "status": row[11],
    }
