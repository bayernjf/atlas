"""统一执行器调试钩子（契约 04 §5.12；挂点 loader._make_executor.execute）。

B 包增强（docs/27 §4）：
- 构造可注入运行级 ``is_cancelled`` 回调：调试流中急停仍抛 DebugStopped
  （保持 event:stopped 语义，不产生重复 cancelled 帧）；
- 断点支持 hit_count（每 N 次命中暂停）与 log_message（日志断点，命中只发
  debug_log 不暂停）；
- resume 声明的 globals 覆盖在续跑前浅合并写回 state["variables"]["global"]
  （action=stop 忽略覆盖；帧本身始终只读深拷贝）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from atlas.graph.conditions import ConditionEvalError, evaluate_expression
from atlas.debug.sessions import DebugSession, DebugStopped

EventCallback = Callable[[dict[str, Any]], None]
CancelCheck = Callable[[], bool]


class DebugController:
    """在 node_start 之后、节点逻辑之前判定暂停；None 控制器零开销。"""

    def __init__(
        self,
        session: DebugSession,
        emit: EventCallback,
        is_cancelled: CancelCheck | None = None,
    ):
        self._session = session
        self._emit = emit
        self._is_cancelled = is_cancelled

    def before_node(self, node: Any, state: dict[str, Any], *, now: datetime | None = None) -> None:
        session = self._session
        # 调试流的运行级急停统一折叠为 DebugStopped（event:stopped），不另发 cancelled。
        if session.cancelled or (self._is_cancelled is not None and self._is_cancelled()):
            session.cancelled = True
            raise DebugStopped(node.id)

        decision = self._classify_hit(node.id, state, now)
        if decision is None:
            return
        if decision[0] == "log":
            _, message, hits = decision
            self._emit(
                {"type": "debug_log", "node_id": node.id, "hits": hits, "message": message}
            )
            return

        reason = decision[1]
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

        overrides = session.take_overrides(token)
        if action == "continue":
            session.step_mode = False
        elif action == "stop":
            session.cancelled = True
        if session.cancelled:
            raise DebugStopped(node.id)
        # step/continue 才写回覆盖（stop 忽略）；浅合并，dict 值整体替换，不删键。
        if action in ("step", "continue") and overrides:
            variables = state.setdefault("variables", {})
            globals_ = variables.setdefault("global", {})
            if isinstance(globals_, dict):
                globals_.update(overrides)

    def _classify_hit(
        self, node_id: str, state: dict[str, Any], now: datetime | None = None
    ) -> tuple[str, Any, Any] | None:
        """返回 ("pause", reason, None) / ("log", message, hits) / None（不暂停）。"""
        session = self._session
        # 单步模式：每个节点都暂停，与断点计数/日志断点互不混用（§4.2 v1 口径）。
        if session.step_mode:
            return ("pause", "step", None)
        spec = session.breakpoints.get(node_id)
        if spec is None:
            return None
        expression = spec.expression
        if expression:
            context = {"global": state["variables"].get("global", {}), **state["outputs"]}
            try:
                hit = evaluate_expression(expression, context, now=now)
            except ConditionEvalError as exc:
                # fail-safe：表达式异常不卡断运行，仅记录供调试侧可见（04 §5.12）。
                session.last_condition_error = str(exc)
                return None
            if not hit:
                return None
            reason = "condition"
        else:
            reason = "breakpoint"

        hits = session.bump_hits(node_id)
        # logpoint：非空消息即只记日志不暂停（v1 消息原样输出，不做插值）。
        if spec.log_message:
            return ("log", spec.log_message, hits)
        # hitCount：仅当 hits % N == 0 才暂停，其它次放行。
        n = spec.hit_count
        if isinstance(n, int) and n > 0 and hits % n != 0:
            return None
        return ("pause", reason, None)
