"""A2A 任务台账 PostgreSQL 持久化（docs/90、docs/73 W3）。

边界：A2A 任务仍是同步执行，`_active_runs` 与 SSE 钩子保持进程内；本表只把
`tasks/get` / `tasks/cancel` / 幂等重放需要的任务快照与幂等键跨重启保留下来。
`ATLAS_STORAGE_BACKEND=pg` 时由 `atlas.a2a.rpc` 装配本类；缺省 memory 档不变。
"""

from __future__ import annotations

import json
import time
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine


def _payload(value: Any) -> dict[str, Any]:
    if isinstance(value, (str, bytes, bytearray)):
        return json.loads(value)
    return dict(value)


class PgA2ALedger:
    """单租户无关的 A2A 任务表；任务 ID 为 UUID，按 task_id 主键。"""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @staticmethod
    def _attach(row) -> dict[str, Any]:
        task = _payload(row.task)
        task["_expires_at"] = float(row.expires_at)
        task["_tenant_id"] = row.tenant_id or ""
        if row.idempotency_key:
            task["_idempotency_key"] = row.idempotency_key
            task["_idempotency_fingerprint"] = row.fingerprint or ""
        return task

    def put(
        self,
        task: dict[str, Any],
        *,
        expires_at: float,
        tenant_id: str = "",
        idempotency_key: str | None = None,
        fingerprint: str | None = None,
    ) -> None:
        state = (task.get("status") or {}).get("state", "unknown")
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO a2a_tasks "
                    "(task_id, tenant_id, state, expires_at, idempotency_key, fingerprint, task) "
                    "VALUES (:task_id, :tenant_id, :state, :expires_at, :idempotency_key, "
                    ":fingerprint, CAST(:task AS JSONB)) "
                    "ON CONFLICT (task_id) DO UPDATE SET "
                    "tenant_id=EXCLUDED.tenant_id, state=EXCLUDED.state, "
                    "expires_at=EXCLUDED.expires_at, task=EXCLUDED.task, "
                    "updated_at=CURRENT_TIMESTAMP, "
                    "idempotency_key=COALESCE(EXCLUDED.idempotency_key, a2a_tasks.idempotency_key), "
                    "fingerprint=COALESCE(EXCLUDED.fingerprint, a2a_tasks.fingerprint)"
                ),
                {
                    "task_id": task["id"],
                    "tenant_id": tenant_id,
                    "state": state,
                    "expires_at": expires_at,
                    "idempotency_key": idempotency_key,
                    "fingerprint": fingerprint,
                    "task": json.dumps(task, ensure_ascii=False, default=str),
                },
            )

    def get(self, task_id: str, *, now: float | None = None) -> dict[str, Any] | None:
        now_value = now if now is not None else time.time()
        with self._engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT task_id, tenant_id, expires_at, idempotency_key, fingerprint, task "
                    "FROM a2a_tasks WHERE task_id=:task_id AND expires_at > :now"
                ),
                {"task_id": task_id, "now": now_value},
            ).mappings().first()
            return self._attach(row) if row is not None else None

    def find_by_idempotency(
        self, tenant_id: str, key: str, *, now: float | None = None
    ) -> tuple[dict[str, Any], str] | None:
        now_value = now if now is not None else time.time()
        with self._engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT task_id, tenant_id, expires_at, idempotency_key, fingerprint, task "
                    "FROM a2a_tasks WHERE tenant_id=:tenant_id AND idempotency_key=:key "
                    "AND expires_at > :now ORDER BY updated_at DESC LIMIT 1"
                ),
                {"tenant_id": tenant_id, "key": key, "now": now_value},
            ).mappings().first()
            if row is None:
                return None
            return self._attach(row), row.fingerprint or ""

    def delete(self, task_id: str) -> None:
        with self._engine.begin() as conn:
            conn.execute(text("DELETE FROM a2a_tasks WHERE task_id=:task_id"), {"task_id": task_id})

    def prune(self, now: float) -> int:
        with self._engine.begin() as conn:
            result = conn.execute(text("DELETE FROM a2a_tasks WHERE expires_at <= :now"), {"now": now})
            return int(result.rowcount or 0)

    def count(self) -> int:
        with self._engine.connect() as conn:
            return int(conn.execute(text("SELECT COUNT(*) FROM a2a_tasks")).scalar_one())
