"""进程内调试会话与暂停状态机（契约 04 §5.12，06 §6.10）。

与 ApprovalBroker 同构（token + threading.Event + REST 首决生效）但语义不同：
无超时、无路由，携带 step/continue/stop 状态机与断点表，故另建不合并。
每会话同时刻只有一个活动暂停；parallel 分支经会话门闩锁串行进入暂停区。
进程内、重启即失；持久化中断-恢复缓做 docs/14 D27（与 D19/D20 同批）。
"""

from __future__ import annotations

import copy
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

DebugAction = Literal["step", "continue", "stop"]


class DebugStopped(Exception):
    """resume action=stop（或 reset 释放）后从暂停点冒泡终止本次 run。"""

    def __init__(self, node_id: str):
        super().__init__(f"debug stopped at {node_id}")
        self.node_id = node_id


@dataclass
class _Pause:
    token: str
    node_id: str
    node_type: str
    reason: str
    globals: dict[str, Any]
    outputs: dict[str, Any]
    event: threading.Event
    action: DebugAction | None = None


@dataclass
class DebugSession:
    graph_id: str
    breakpoints: dict[str, str | None]
    session_id: str = field(default_factory=lambda: "dbg-" + uuid.uuid4().hex)
    step_mode: bool = True
    cancelled: bool = False
    last_condition_error: str | None = None
    # _gate 由暂停线程在整个 request→wait 区间持有，串行化并行分支。
    _gate: threading.Lock = field(default_factory=threading.Lock)
    _state: threading.Lock = field(default_factory=threading.Lock)
    _pause: _Pause | None = None

    def request_pause(
        self, *, node_id: str, node_type: str, reason: str, globals: dict, outputs: dict
    ) -> str:
        self._gate.acquire()
        try:
            token = "dbg-" + uuid.uuid4().hex
            pause = _Pause(
                token=token,
                node_id=node_id,
                node_type=node_type,
                reason=reason,
                globals=copy.deepcopy(globals),
                outputs=copy.deepcopy(outputs),
                event=threading.Event(),
            )
            with self._state:
                self._pause = pause
            return token
        except BaseException:
            self._gate.release()
            raise

    def wait(self, token: str) -> DebugAction:
        with self._state:
            pause = self._pause
        assert pause is not None and pause.token == token
        pause.event.wait()
        with self._state:
            action = pause.action
        return action or "stop"

    def end_pause(self, token: str) -> None:
        # 保留已决暂停供重复 resume 判定 409（投影已隐藏已决项）；下次暂停覆盖 _pause。
        self._gate.release()

    def resolve(self, token: str, action: DebugAction) -> bool:
        """首决生效：True=本次完成恢复，False=未知 token 或该暂停已决。"""
        with self._state:
            pause = self._pause
            if pause is None or pause.token != token or pause.action is not None:
                return False
            pause.action = action
            pause.event.set()
        return True

    def projection(self) -> dict[str, Any] | None:
        with self._state:
            pause = self._pause
            if pause is None or pause.action is not None:
                return None
            return {
                "token": pause.token,
                "node_id": pause.node_id,
                "node_type": pause.node_type,
                "graph_id": self.graph_id,
                "reason": pause.reason,
            }

    def frame(self, token: str) -> dict[str, Any] | None:
        with self._state:
            pause = self._pause
            if pause is None or pause.token != token:
                return None
            return {
                "type": "paused",
                "token": pause.token,
                "node_id": pause.node_id,
                "node_type": pause.node_type,
                "reason": pause.reason,
                "globals": copy.deepcopy(pause.globals),
                "outputs": copy.deepcopy(pause.outputs),
            }


@dataclass
class DebuggerBroker:
    """全部调试会话；进程内单例，重启清空。"""

    _sessions: dict[str, DebugSession] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def create(
        self, *, graph_id: str, breakpoints: list[dict[str, Any]] | None
    ) -> DebugSession:
        table = {
            str(bp["node_id"]): (str(bp["expression"]) if bp.get("expression") else None)
            for bp in (breakpoints or [])
        }
        session = DebugSession(graph_id=graph_id, breakpoints=table)
        with self._lock:
            self._sessions[session.session_id] = session
        return session

    def get_session(self, token: str) -> DebugSession | None:
        with self._lock:
            sessions = list(self._sessions.values())
        for session in sessions:
            with session._state:
                pause = session._pause
            if pause is not None and pause.token == token:
                return session
        return None

    def list_pending(self) -> list[dict[str, Any]]:
        with self._lock:
            sessions = list(self._sessions.values())
        items: list[dict[str, Any]] = []
        for session in sessions:
            projection = session.projection()
            if projection is not None:
                items.append(projection)
        return items

    def reset(self) -> None:
        """Demo reset：按 stop 释放全部暂停线程（防悬挂）并清空会话。"""
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            with session._state:
                session.cancelled = True
                pause = session._pause
                if pause is not None and pause.action is None:
                    pause.action = "stop"
                    pause.event.set()
