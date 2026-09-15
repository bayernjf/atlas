"""统一执行器调试钩子（契约 04 §5.12；挂点 loader._make_executor.execute）。"""

from __future__ import annotations

from typing import Any, Callable

from atlas.graph.conditions import ConditionEvalError, evaluate_expression
from atlas.debug.sessions import DebugSession, DebugStopped

EventCallback = Callable[[dict[str, Any]], None]


class DebugController:
    """在 node_start 之后、节点逻辑之前判定暂停；None 控制器零开销。"""

    def __init__(self, session: DebugSession, emit: EventCallback):
        self._session = session
        self._emit = emit

    def before_node(self, node: Any, state: dict[str, Any]) -> None:
        session = self._session
        if session.cancelled:
            raise DebugStopped(node.id)

        reason = self._hit_reason(node.id, state)
        if reason is None:
            return

        token = session.request_pause(
            node_id=node.id,
            node_type=node.type,
            reason=reason,
            globals=state["variables"].get("global", {}),
            outputs=state["outputs"],
        )
        frame = session.frame(token)
        self._emit(frame)
        try:
            action = session.wait(token)
        finally:
            session.end_pause(token)

        if action == "continue":
            session.step_mode = False
        elif action == "stop":
            session.cancelled = True
        if session.cancelled:
            raise DebugStopped(node.id)

    def _hit_reason(self, node_id: str, state: dict[str, Any]) -> str | None:
        session = self._session
        if session.step_mode:
            return "step"
        if node_id not in session.breakpoints:
            return None
        expression = session.breakpoints[node_id]
        if not expression:
            return "breakpoint"
        context = {"global": state["variables"].get("global", {}), **state["outputs"]}
        try:
            hit = evaluate_expression(expression, context)
        except ConditionEvalError as exc:
            # fail-safe：表达式异常不卡断运行，仅记录供调试侧可见（04 §5.12）。
            session.last_condition_error = str(exc)
            return None
        return "condition" if hit else None
