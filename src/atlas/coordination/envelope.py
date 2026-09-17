"""任务信封（M7，docs/19 §2.3.1 提案转权威 + 08 M7 立项条 + 03 task_envelope）。

确定性 Graph 主干不动（19 §2.1：LLM 无控制流写入权），任务信封是协调者与 Bot
之间的执行单元。`TaskState` 状态机 dispatch `pending→accepted→running→
done/failed/timeout`，终态不可再迁移；幂等去重、CAS、升级在 store/loader 层。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

TaskState = Literal["pending", "accepted", "running", "done", "failed", "timeout"]

# 合法迁移表（dispatch 状态机，19 §2.2.2）；终态无出边。
_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "pending": ("accepted",),
    "accepted": ("running",),
    "running": ("done", "failed", "timeout"),
    "done": (),
    "failed": (),
    "timeout": (),
}

TERMINAL_STATES = frozenset({"done", "failed", "timeout"})


def can_transition(current: str, target: str) -> bool:
    """状态机合法性：仅允许 _TRANSITIONS 内的迁移；终态不可迁移。"""
    return target in _TRANSITIONS.get(current, ())


class Envelope(BaseModel):
    """任务信封（字段照 19 §2.3.1，03 task_envelope 转权威）。"""

    taskId: str
    runId: str
    idempotencyKey: str
    traceId: str
    graphVersion: str
    type: str
    assignee: str
    payload: dict[str, Any] = Field(default_factory=dict)
    deadlineMs: int
    state: TaskState = "pending"
    result: dict[str, Any] = Field(default_factory=dict)
    attempt: int = 1

    def transition(self, target: TaskState) -> "Envelope":
        """迁移状态；非法迁移抛 ValueError（返回新实例，原信封不可变）。"""
        if not can_transition(self.state, target):
            raise ValueError(f"非法任务状态迁移：{self.state} → {target}")
        return self.model_copy(update={"state": target})
