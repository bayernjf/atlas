"""T4：任务总线 SQLite 持久化实现（同一 `TaskStore` 接口）。

默认仍走进程内 `TaskStore`；`ATLAS_TASK_STORE_PATH` 配置后 `TenantRegistry`
按租户装配本类。表以 `(tenant_id, task_id)` 为界，JSON 存完整 Envelope，
`seq` 保留新→旧顺序。跨进程重启只靠这张表恢复 pending/running/终态与冲突键。

并发度按本机单进程/少量 Bot 设计：`BEGIN IMMEDIATE` 一把锁，不做连接池；
冲突仲裁与内存实现共用 `mark_preempted`，避免两档语义漂移。
"""

from __future__ import annotations

import sqlite3
import threading
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any

from .envelope import Envelope, TERMINAL_STATES, TaskState
from .store import mark_preempted


class SqliteTaskStore:
    def __init__(self, path: str | Path, tenant_id: str) -> None:
        self.path = str(path)
        self.tenant_id = tenant_id
        self._lock = threading.Lock()
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS task_envelopes (
                    tenant_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    envelope TEXT NOT NULL,
                    UNIQUE(tenant_id, task_id),
                    UNIQUE(tenant_id, idempotency_key)
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _row_to_envelope(self, row: sqlite3.Row) -> Envelope:
        return Envelope.model_validate_json(row["envelope"])

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
        with self._lock, closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                existing = conn.execute(
                    "SELECT envelope FROM task_envelopes "
                    "WHERE tenant_id=? AND idempotency_key=?",
                    (self.tenant_id, idempotency_key),
                ).fetchone()
                if existing is not None:
                    conn.execute("COMMIT")
                    return self._row_to_envelope(existing), False

                task_id = uuid.uuid4().hex
                if conflict_key:
                    rows = conn.execute(
                        "SELECT envelope FROM task_envelopes "
                        "WHERE tenant_id=? ORDER BY seq DESC",
                        (self.tenant_id,),
                    ).fetchall()
                    active = None
                    for row in rows:
                        candidate = self._row_to_envelope(row)
                        if (
                            candidate.conflictKey == conflict_key
                            and candidate.state not in TERMINAL_STATES
                        ):
                            active = candidate
                            break
                    if active is not None:
                        if priority > active.priority:
                            update = mark_preempted(
                                active, by_task_id=task_id, conflict_key=conflict_key
                            )
                            conn.execute(
                                "UPDATE task_envelopes SET envelope=? "
                                "WHERE tenant_id=? AND task_id=?",
                                (update.model_dump_json(), self.tenant_id, active.taskId),
                            )
                        else:
                            conn.execute("COMMIT")
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
                    "INSERT INTO task_envelopes "
                    "(tenant_id, task_id, idempotency_key, envelope) VALUES (?, ?, ?, ?)",
                    (
                        self.tenant_id,
                        envelope.taskId,
                        envelope.idempotencyKey,
                        envelope.model_dump_json(),
                    ),
                )
                conn.execute("COMMIT")
                return envelope, True
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def _mutate(self, task_id: str, target: TaskState, **updates: Any) -> Envelope:
        with self._lock, closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT envelope FROM task_envelopes "
                "WHERE tenant_id=? AND task_id=?",
                (self.tenant_id, task_id),
            ).fetchone()
            if row is None:
                raise KeyError(task_id)
            envelope = self._row_to_envelope(row)
            envelope = envelope.transition(target).model_copy(update=updates)
            conn.execute(
                "UPDATE task_envelopes SET envelope=? "
                "WHERE tenant_id=? AND task_id=?",
                (envelope.model_dump_json(), self.tenant_id, task_id),
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
        with self._lock, closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT envelope FROM task_envelopes "
                "WHERE tenant_id=? AND task_id=?",
                (self.tenant_id, task_id),
            ).fetchone()
            return self._row_to_envelope(row) if row is not None else None

    def list(self, state: TaskState | None = None) -> list[Envelope]:
        with self._lock, closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT envelope FROM task_envelopes "
                "WHERE tenant_id=? ORDER BY seq DESC",
                (self.tenant_id,),
            ).fetchall()
            items = [self._row_to_envelope(row) for row in rows]
        if state:
            items = [envelope for envelope in items if envelope.state == state]
        return items

    def reset(self) -> None:
        with self._lock, closing(self._connect()) as conn:
            conn.execute(
                "DELETE FROM task_envelopes WHERE tenant_id=?", (self.tenant_id,)
            )
