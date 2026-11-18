"""LLM 用量计量 PG 档（docs/129 打包 AU＝D70；迁移 049 `llm_usage`）。

与进程内 `InMemoryUsageStore` **方法签名一比一**（record/list_recent/summarize/reset），
故 metering helper、TenantServices 装配与 REST 两端点零分叉。形状照
`reflection/pg_store.py`：行内 tenant_id 过滤（非 RLS）、每租户一实例。

两点与内存档的**刻意差异**（docs/129 AU-D2）：
- **不设 ring**：PG 是成本账的权威档，淘汰即丢账；内存档 ring 2000 只是
  Demo/无 PG 形态的有界缓冲（超出覆盖最旧）。
- **retention 已挂**（2026-10-16 AU-D7 余部闭合）：走 docs/65 K-A 那套
  分表 env `ATLAS_RETENTION_LLM_USAGE_DAYS`（缺省回退全局
  `ATLAS_RETENTION_DAYS`，再回退默认 90 天，与 audit/runs 成本账对齐）；
  立即清零仍走 `/api/demo/reset`。

渐进 ORM 化批 1（方案 C）：内部 SQL 迁到 SQLAlchemy ORM（`storage/models.py`
`LlmUsageORM`，同一 sync Engine 上叠 sessionmaker），对外构造签名与方法面不变。
读取走**列级 select**（返回 Row、不经 identity map）：表无数据库主键、完全相同的
重复行不得去重，行数/聚合语义与原生 SQL 一致。058 补列（assignment_id/
billing_source）record 不写、靠 server_default，行为不变。
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from sqlalchemy import Engine, case, delete, desc, func, literal_column, select
from sqlalchemy.orm import sessionmaker

from atlas.storage.models import LlmUsageORM

from .metering import UsageRecord

logger = logging.getLogger(__name__)

# group_by 白名单（REST 层已校验；双保险）。ORM 化后防注入天然成立：
# 非法 key 回退 caller，表达式是 SQLAlchemy 列对象而非字符串拼接。
_GROUP_KEY: dict[str, Callable[[], Any]] = {
    "caller": lambda: LlmUsageORM.caller,
    "model": lambda: LlmUsageORM.model,
    "day": lambda: func.substr(LlmUsageORM.ts, 1, 10),
}


class PgUsageStore:
    """`llm_usage` 表 PG 实现（每租户一实例，行内 tenant_id 过滤）。"""

    def __init__(self, engine: Engine, tenant_id: str) -> None:
        self._engine = engine
        self._tenant_id = tenant_id
        self._session_factory = sessionmaker(bind=engine)

    def record(self, rec: UsageRecord) -> None:
        with self._session_factory.begin() as session:
            session.add(
                LlmUsageORM(
                    tenant_id=rec.tenant_id,
                    ts=rec.ts,
                    caller=rec.caller,
                    node_id=rec.node_id,
                    graph_id=rec.graph_id,
                    run_id=rec.run_id,
                    model=rec.model,
                    prompt_tokens=rec.prompt_tokens,
                    completion_tokens=rec.completion_tokens,
                    total_tokens=rec.total_tokens,
                    latency_ms=rec.latency_ms,
                    status=rec.status,
                )
            )

    def list_recent(self, limit: int = 50) -> list[dict]:
        bounded = max(1, min(int(limit), 200))
        stmt = (
            select(
                LlmUsageORM.tenant_id,
                LlmUsageORM.ts,
                LlmUsageORM.caller,
                LlmUsageORM.node_id,
                LlmUsageORM.graph_id,
                LlmUsageORM.run_id,
                LlmUsageORM.model,
                LlmUsageORM.prompt_tokens,
                LlmUsageORM.completion_tokens,
                LlmUsageORM.total_tokens,
                LlmUsageORM.latency_ms,
                LlmUsageORM.status,
            )
            .where(LlmUsageORM.tenant_id == self._tenant_id)
            .order_by(LlmUsageORM.ts.desc())
            .limit(bounded)
        )
        with self._session_factory() as session:
            rows = session.execute(stmt).all()
        return [_row_view(row) for row in rows]

    def summarize(
        self,
        *,
        since: str | None = None,
        until: str | None = None,
        group_by: str = "caller",
    ) -> list[dict]:
        key_expr = _GROUP_KEY.get(group_by, _GROUP_KEY["caller"])()
        # CASE 聚合：status='error' 计 1，否则 0（与原 SQL `SUM(CASE WHEN ...)` 逐字语义）。
        errors = func.coalesce(
            func.sum(case((LlmUsageORM.status == "error", 1), else_=0)), 0
        ).label("errors")
        stmt = select(
            key_expr.label("key"),
            func.count().label("calls"),
            func.coalesce(func.sum(LlmUsageORM.prompt_tokens), 0).label("prompt_tokens"),
            func.coalesce(func.sum(LlmUsageORM.completion_tokens), 0).label("completion_tokens"),
            func.coalesce(func.sum(LlmUsageORM.total_tokens), 0).label("total_tokens"),
            errors,
        ).where(LlmUsageORM.tenant_id == self._tenant_id)
        if since:
            stmt = stmt.where(LlmUsageORM.ts >= since)
        if until:
            stmt = stmt.where(LlmUsageORM.ts <= until)
        # GROUP BY 重复完整 key 表达式；ORDER BY 输出别名 total_tokens（原 SQL 语义）。
        stmt = stmt.group_by(key_expr).order_by(desc(literal_column("total_tokens")))
        with self._session_factory() as session:
            rows = session.execute(stmt).all()
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
        with self._session_factory.begin() as session:
            session.execute(delete(LlmUsageORM).where(LlmUsageORM.tenant_id == self._tenant_id))


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
