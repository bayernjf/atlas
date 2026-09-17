"""任务信封执行层（M7，多 Bot 任务总线；08 M7 立项条 + ADR T20）。"""

from .envelope import Envelope, TaskState, TERMINAL_STATES, can_transition
from .store import TaskStore

__all__ = [
    "Envelope",
    "TaskState",
    "TERMINAL_STATES",
    "TaskStore",
    "can_transition",
]
