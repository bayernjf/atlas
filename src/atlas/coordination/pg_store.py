"""T4：任务总线 PostgreSQL 持久化实现（同一 `TaskStore` 接口）。

`ATLAS_STORAGE_BACKEND=pg` 时 `TenantRegistry` 按租户装配本类；表以
`(tenant_id, task_id)` 为界，`envelope` 用 JSONB 存完整信封，`seq` 保留新→旧顺序。
冲突仲裁与内存/SQLite 实现共用 `mark_preempted`，两档语义不漂移。

并发：每租户一把事务级 advisory lock，dispatch／状态迁移在同一事务内串行；
`ON CONFLICT` 兜底幂等键唯一约束。单副本仍是硬约束，本表只解决跨重启持久化。
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from .envelope import Envelope, TaskState
from .store import mark_preempted

_TERMINAL_SQL = "('done','failed','timeout')"


class PgTaskStore:
    def __init__(self, engine: Engine, tenant_id: str) -> None:
        self._engine = engine
        self._tenant_id = tenant_id

    def _lock(self, conn) -> None:
        conn.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:kid))"),
            {"kid": f"atlas-task-bus:{self._tenant_id}"},
        )

    @staticmethod
    def _row_to_envelope(row) -> Envelope:
        value = row.envelope
        if isinstance(value, (str, bytes, bytearray)):
            return Envelope.model_validate_json(value)
        return Envelope.model_validate(value)

    def dispatch(
        self,
        *,
        run_id: str,
        idempotency_key: str,
        trace_id: str,
        graph_version: str,
        type: str,
        assignee: str,
        payload: dict[str, Any],
        deadline_ms: int,
        parent_span_id: str = "",
        conflict_key: str = "",
        priority: int = 0,
    ) -> tuple[Envelope, bool]:
        conflict_key = (conflict_key or "").strip()
        with self._engine.begin() as conn:
            self._lock(conn)
            existing = conn.execute(
                text(
                    "SELECT envelope FROM task_envelopes "
                    "WHERE tenant_id=:tenant_id AND idempotency_key=:idempotency_key"
                ),
                {"tenant_id": self._tenant_id, "idempotency_key": idempotency_key},
            ).mappings().first()
            if existing is not None:
                return self._row_to_envelope(existing), False

            task_id = uuid.uuid4().hex
            if conflict_key:
                rows = conn.execute(
                    text(
                        "SELECT envelope FROM task_envelopes "
                        "WHERE tenant_id=:tenant_id AND conflict_key=:conflict_key "
                        f"AND state NOT IN {_TERMINAL_SQL} "
                        "ORDER BY seq DESC"
                    ),
                    {"tenant_id": self._tenant_id, "conflict_key": conflict_key},
                ).mappings().all()
                active = self._row_to_envelope(rows[0]) if rows else None
                if active is not None:
                    if priority > active.priority:
                        preempted = mark_preempted(
                            active, by_task_id=task_id, conflict_key=conflict_key
                        )
                        conn.execute(
                            text(
                                "UPDATE task_envelopes SET envelope=CAST(:envelope AS JSONB), "
                                "state=:state WHERE tenant_id=:tenant_id AND task_id=:task_id"
                            ),
                            {
                                "envelope": preempted.model_dump_json(),
                                "state": preempted.state,
                                "tenant_id": self._tenant_id,
                                "task_id": active.taskId,
                            },
                        )
                    else:
                        return active, False

            envelope = Envelope(
                taskId=task_id,
                runId=run_id,
                idempotencyKey=idempotency_key,
                traceId=trace_id,
                graphVersion=graph_version,
                type=type,
                assignee=assignee,
                payload=payload,
                deadlineMs=deadline_ms,
                parentSpanId=parent_span_id,
                conflictKey=conflict_key,
                priority=priority,
            )
            conn.execute(
                text(
                    "INSERT INTO task_envelopes "
                    "(tenant_id, task_id, idempotency_key, state, conflict_key, priority, envelope) "
                    "VALUES (:tenant_id, :task_id, :idempotency_key, :state, :conflict_key, "
                    ":priority, CAST(:envelope AS JSONB))"
                ),
                {
                    "tenant_id": self._tenant_id,
                    "task_id": envelope.taskId,
                    "idempotency_key": idempotency_key,
                    "state": envelope.state,
                    "conflict_key": conflict_key,
                    "priority": priority,
                    "envelope": envelope.model_dump_json(),
                },
            )
            return envelope, True

    def _mutate(self, task_id: str, target: TaskState, **updates: Any) -> Envelope:
        with self._engine.begin() as conn:
            self._lock(conn)
            row = conn.execute(
                text(
                    "SELECT envelope FROM task_envelopes "
                    "WHERE tenant_id=:tenant_id AND task_id=:task_id"
                ),
                {"tenant_id": self._tenant_id, "task_id": task_id},
            ).mappings().first()
            if row is None:
                raise KeyError(task_id)
            envelope = self._row_to_envelope(row)
            envelope = envelope.transition(target).model_copy(update=updates)
            conn.execute(
                text(
                    "UPDATE task_envelopes SET envelope=CAST(:envelope AS JSONB), state=:state "
                    "WHERE tenant_id=:tenant_id AND task_id=:task_id"
                ),
                {
                    "envelope": envelope.model_dump_json(),
                    "state": envelope.state,
                    "tenant_id": self._tenant_id,
                    "task_id": task_id,
                },
            )
            return envelope

    def accept(self, task_id: str) -> Envelope:
        return self._mutate(task_id, "accepted")

    def start(self, task_id: str) -> Envelope:
        return self._mutate(task_id, "running")

    def complete(self, task_id: str, result: dict[str, Any]) -> Envelope:
        return self._mutate(task_id, "done", result=result)

    def fail(self, task_id: str, error: str) -> Envelope:
        return self._mutate(task_id, "failed", result={"error": error})

    def timeout(self, task_id: str) -> Envelope:
        return self._mutate(task_id, "timeout")

    def get(self, task_id: str) -> Envelope | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT envelope FROM task_envelopes "
                    "WHERE tenant_id=:tenant_id AND task_id=:task_id"
                ),
                {"tenant_id": self._tenant_id, "task_id": task_id},
            ).mappings().first()
            return self._row_to_envelope(row) if row is not None else None

    def list(self, state: TaskState | None = None) -> list[Envelope]:
        with self._engine.connect() as conn:
            if state:
                rows = conn.execute(
                    text(
                        "SELECT envelope FROM task_envelopes "
                        "WHERE tenant_id=:tenant_id AND state=:state ORDER BY seq DESC"
                    ),
                    {"tenant_id": self._tenant_id, "state": state},
                ).mappings().all()
            else:
                rows = conn.execute(
                    text(
                        "SELECT envelope FROM task_envelopes "
                        "WHERE tenant_id=:tenant_id ORDER BY seq DESC"
                    ),
                    {"tenant_id": self._tenant_id},
                ).mappings().all()
            return [self._row_to_envelope(row) for row in rows]

    def reset(self) -> None:
        with self._engine.begin() as conn:
            self._lock(conn)
            conn.execute(
                text("DELETE FROM task_envelopes WHERE tenant_id=:tenant_id"),
                {"tenant_id": self._tenant_id},
            )
