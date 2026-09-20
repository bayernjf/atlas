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
        # docs/28 §3.3：子图重入时压入命名空间 emit（paused/debug_log 附 subgraphPath），
        # 同线程顺序重入、finally 弹出；session/门闩仍唯一共享。
        self._emit_stack: list[EventCallback] = []

    def push_namespaced_emit(self, namespaced: EventCallback) -> None:
        self._emit_stack.append(self._emit)
        self._emit = namespaced

    def pop_namespaced_emit(self) -> None:
        if self._emit_stack:
            self._emit = self._emit_stack.pop()

    def before_node(self, node: Any, state: dict[str, Any], *, now: datetime | None = None) -> None:
        session = self._session
        # 调试流的运行级急停统一折叠为 DebugStopped（event:stopped），不另发 cancelled。
        if session.cancelled or (self._is_cancelled is not None and self._is_cancelled()):
            session.cancelled = True
            raise DebugStopped(node.id)

        # docs/28 §3.1：登记「自上次暂停以来到达 before 的节点」（当前节点逻辑尚未执行）。
        session.mark_node(node.id)

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
        globals_now = state["variables"].get("global", {})
        # docs/28 §3.1：暂停成立先沉淀 global 顶层键变化历史，再入暂停。
        session.snapshot_change(node_id=node.id, reason=reason, globals_=globals_now)
        token = session.request_pause(
            node_id=node.id,
            node_type=node.type,
            reason=reason,
            globals=globals_now,
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
                # 用户手动改写同步进历史基线，不被记为运行变化（docs/28 §3.1）。
                session.seed_baseline(globals_)

    def on_exception(self, node: Any, exc: BaseException, state: dict[str, Any]) -> None:
        """节点逻辑抛异常时调用（docs/28 §3.2）。

        该节点配置异常断点则先暂停供观测；wait 返回 stop 抛 DebugStopped，
        step/continue 正常返回（由 executor 原样重抛 exc，v1 不提供忽略继续）。
        未配置则立即返回，executor 随即重抛，行为与现状逐字节一致。
        """
        session = self._session
        spec = session.breakpoints.get(node.id)
        if spec is None or not spec.exception:
            return
        globals_now = state["variables"].get("global", {})
        session.snapshot_change(
            node_id=node.id, reason="exception", globals_=globals_now
        )
        token = session.request_pause(
            node_id=node.id,
            node_type=node.type,
            reason="exception",
            globals=globals_now,
            outputs=state["outputs"],
        )
        frame = session.frame(token)
        # 帧纯超集加 error（仅改本次下发的本地 dict，不污染会话内投影）。
        frame["error"] = {"type": type(exc).__name__, "message": str(exc)}
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
        if action in ("step", "continue") and overrides:
            variables = state.setdefault("variables", {})
            globals_ = variables.setdefault("global", {})
            if isinstance(globals_, dict):
                globals_.update(overrides)
                session.seed_baseline(globals_)
        # step/continue：正常返回，executor 的 except 块原样重抛原异常。

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
        # docs/28 §3.2：纯异常断点（只开 onException，无条件表达式/hitCount/logMessage）
        # 不在节点逻辑前暂停，仅在节点抛异常时由 on_exception 暂停。
        if (
            spec.exception
            and not spec.expression
            and not spec.hit_count
            and not spec.log_message
        ):
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
