"""任务信封存储（M7，进程内首版，08 M7 立项条 + ADR T20）。

按租户分区由 `TenantRegistry` 承载（每租户一实例，构造期关切不进方法签名）；
`dispatch` 幂等去重（idempotencyKey 命中返首结果，不重复建任务，19 §2.4 L1）。
状态机迁移与 deadline 语义在 `envelope.py`；超时三级链的动作链（重试→升级→
fail-safe）在批 2/3 的 loader 层接线。
"""

from __future__ import annotations

import threading
import uuid
from typing import Any

from .envelope import Envelope, TaskState


class TaskStore:
    def __init__(self) -> None:
        self._tasks: dict[str, Envelope] = {}
        self._by_key: dict[str, str] = {}  # idempotencyKey -> taskId
        self._order: list[str] = []
        self._lock = threading.Lock()

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
    ) -> tuple[Envelope, bool]:
        """创建 pending 信封；幂等键命中返首结果（created=False，不重复执行）。"""
        with self._lock:
            existing = self._by_key.get(idempotency_key)
            if existing is not None:
                return self._tasks[existing], False
            task_id = uuid.uuid4().hex
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
            )
            self._tasks[task_id] = envelope
            self._by_key[idempotency_key] = task_id
            self._order.append(task_id)
            return envelope, True

    def _mutate(self, task_id: str, target: TaskState, **updates: Any) -> Envelope:
        with self._lock:
            envelope = self._tasks[task_id]
            self._tasks[task_id] = envelope.transition(target).model_copy(update=updates)
            return self._tasks[task_id]

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
        with self._lock:
            return self._tasks.get(task_id)

    def list(self, state: TaskState | None = None) -> list[Envelope]:
        with self._lock:
            items = [self._tasks[task_id] for task_id in reversed(self._order)]
        if state:
            items = [envelope for envelope in items if envelope.state == state]
        return items

    def reset(self) -> None:
        with self._lock:
            self._tasks.clear()
            self._by_key.clear()
            self._order.clear()
