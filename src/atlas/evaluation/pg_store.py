"""打包 AC（docs/110）：evaluation run PG 档 store（迁移 047 `evaluations` 表）。

评估结果含审计价值（谁在什么图上跑过什么评估、结论如何），生产档持久化；
形状照 `PgUserTemplateStore` 惯例：`engine.begin()` 事务、tenant_id 分区、
跨租户不可见（表主键含 tenant_id，查询恒带租户条件）。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import Engine, text

from .models import EvaluationRun


def _jsonb(value: Any) -> Any:
    """JSONB 列 psycopg 已自动解为 dict 时原样返回（照 pg_reports 惯例）。"""
    return json.loads(value) if isinstance(value, str) else value


class PgEvaluationStore:
    def __init__(self, engine: Engine, tenant_id: str) -> None:
        self._engine = engine
        self._tenant_id = tenant_id

    def save(self, run: EvaluationRun) -> None:
        payload = run.model_dump(mode="json")
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO evaluations (tenant_id, id, task_id, graph_id, summary, cases, created_at)
                    VALUES (:tenant_id, :id, :task_id, :graph_id, :summary, :cases, :created_at)
                    ON CONFLICT (tenant_id, id) DO UPDATE SET
                        summary = EXCLUDED.summary,
                        cases = EXCLUDED.cases
                    """
                ),
                {
                    "tenant_id": self._tenant_id,
                    "id": run.id,
                    "task_id": run.task_id,
                    "graph_id": run.graph_id,
                    "summary": json.dumps(payload["summary"], ensure_ascii=False),
                    "cases": json.dumps(payload["cases"], ensure_ascii=False),
                    "created_at": run.created_at,
                },
            )

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._engine.begin() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT id, task_id, graph_id, summary, cases, created_at
                    FROM evaluations
                    WHERE tenant_id = :tenant_id
                    ORDER BY created_at DESC, id DESC
                    LIMIT :limit
                    """
                ),
                {"tenant_id": self._tenant_id, "limit": limit},
            ).mappings().all()
        items = []
        for row in rows:
            items.append(
                {
                    "id": row["id"],
                    "task_id": row["task_id"],
                    "graph_id": row["graph_id"],
                    "summary": _jsonb(row["summary"]),
                    "cases": _jsonb(row["cases"]),
                    "created_at": row["created_at"],
                }
            )
        return items

    def clear(self) -> None:
        """reset 同清（docs/110 §一.5）：清空本租户评估记录，照 pg_template_store 惯例。"""
        with self._engine.begin() as conn:
            conn.execute(
                text("DELETE FROM evaluations WHERE tenant_id = :tenant_id"),
                {"tenant_id": self._tenant_id},
            )
