"""进程内调试会话与暂停状态机（契约 04 §5.12，06 §6.10；B 包增强 docs/27 §4）。

与 ApprovalBroker 同构（token + threading.Event + REST 首决生效）但语义不同：
无超时、无路由，携带 step/continue/stop 状态机与断点表，故另建不合并。
每会话同时刻只有一个活动暂停；parallel 分支经会话门闩锁串行进入暂停区。
进程内、重启即失；持久化中断-恢复缓做 docs/14 D27（与 D19/D20 同批）。

B 包（docs/27 §4.2/§4.3）：
- 断点由 ``{node_id: expression|None}`` 超集为每节点 BreakpointSpec
  （expression / hit_count 每 N 次命中暂停 / log_message 日志断点不暂停）；
- 会话内易失计数 hit_counts（不持久化）；
- resume 可带 globals 顶层键浅合并覆盖（apply_overrides 校验后暂存本次 _Pause，
  控制器在续跑前写回 state；帧仍是只读深拷贝）。

docs/28 批 2（§3.1）：
- DebugSession 增易失 variable_history（相邻暂停间 global 顶层键新增/变更＋经过节点，
  正序上限 50，不持久化、不进录制）；mark_node/snapshot_change/seed_baseline 三方法，
  frame() 纯超集带 history。
"""

from __future__ import annotations

import copy
import json
import re
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

DebugAction = Literal["step", "continue", "stop"]

# 合法 global 顶层变量名（与条件表达式标识符口径一致）。
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# docs/28 §3.1：变量变化历史上限（正序，超出丢最旧）。
VARIABLE_HISTORY_LIMIT = 50

# 不可 JSON 序列化值的哨兵（变化历史只收可随 paused 帧下发的 JSON 值）。
_UNSTABLE = object()


def _stable_json(value: Any) -> Any:
    """JSON round-trip 净化；不可序列化返回 _UNSTABLE（调用方 fail-safe 跳过该键）。"""
    try:
        return json.loads(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError):
        return _UNSTABLE


class DebugStopped(Exception):
    """resume action=stop（或 reset 释放）后从暂停点冒泡终止本次 run。"""

    def __init__(self, node_id: str):
        super().__init__(f"debug stopped at {node_id}")
        self.node_id = node_id


@dataclass
class BreakpointSpec:
    """单个节点的断点配置（会话级，不落 Graph JSON）。

    log_message 非空即日志断点（logpoint）：命中只发 debug_log 不暂停（v1 与
    hit_count 不强行组合）；hit_count 为正整数 N 时每第 N 次命中才暂停。
    """

    expression: str | None = None
    hit_count: int | None = None
    log_message: str | None = None


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
    # B 包：本次 resume 声明的 globals 顶层键覆盖（resolve 前由 apply_overrides 暂存）。
    overrides: dict[str, Any] | None = None


@dataclass
class DebugSession:
    graph_id: str
    breakpoints: dict[str, BreakpointSpec]
    session_id: str = field(default_factory=lambda: "dbg-" + uuid.uuid4().hex)
    step_mode: bool = True
    cancelled: bool = False
    last_condition_error: str | None = None
    # B 包：会话内每节点累计命中次数（logpoint/暂停共用）。
    hit_counts: dict[str, int] = field(default_factory=dict)
    # docs/28 §3.1：相邻暂停间 global 顶层键变化历史（易失、正序、上限 50）。
    variable_history: list[dict[str, Any]] = field(default_factory=list)
    _last_pause_globals: dict[str, Any] | None = None
    _nodes_since_pause: list[str] = field(default_factory=list)
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

    def apply_overrides(self, token: str, globals_: dict[str, Any]) -> None:
        """resume 带 globals 时在 resolve 前调用：校验并暂存本次覆盖（B 包 §4.3）。

        仅允许 global 作用域顶层键、合法标识符、JSON 可序列化；浅合并语义
        （提供的键整体替换值，未提供的不动）在控制器续跑前写回。非法抛 ValueError
        （API 层转中文 422）；暂停未知/已决抛 ValueError（不改变其状态）。
        """
        if not isinstance(globals_, dict):
            raise ValueError("globals 必须为对象（仅支持 global 作用域顶层键）")
        cleaned: dict[str, Any] = {}
        for key, value in globals_.items():
            if not isinstance(key, str) or not _IDENT_RE.match(key):
                raise ValueError(f"非法 global 变量名：{key!r}（仅允许合法标识符顶层键）")
            try:
                json.dumps(value)
            except (TypeError, ValueError):
                raise ValueError(f"global 变量 {key} 的值必须是 JSON 可序列化的")
            cleaned[key] = copy.deepcopy(value)
        with self._state:
            pause = self._pause
            if pause is None or pause.token != token or pause.action is not None:
                raise ValueError("调试暂停不存在或已恢复，无法应用变量覆盖")
            pause.overrides = cleaned

    def take_overrides(self, token: str) -> dict[str, Any]:
        """续跑前取出本次暂停暂存的覆盖（深拷贝）；无则空 dict。"""
        with self._state:
            pause = self._pause
            if pause is None or pause.token != token:
                return {}
            return copy.deepcopy(pause.overrides or {})

    def mark_node(self, node_id: str) -> None:
        """节点 before_node 开头调用（取消检查后）：登记自上次暂停以来到达的节点。

        语义为「到达 before 的节点」，当前节点逻辑尚未执行，归入本次区间起点。
        """
        with self._state:
            self._nodes_since_pause.append(node_id)

    def snapshot_change(self, *, node_id: str, reason: str, globals_: Any) -> None:
        """暂停成立、request_pause 之前调用：与上次暂停基线做 global 顶层键 diff。

        仅记新增（old=None）与值变更；键删除 v1 不记（resume 浅合并只加不删）。
        不可 JSON 序列化的键 fail-safe 跳过；记完清空经过节点、置本次为新基线。
        """
        with self._state:
            # 基线只保留 JSON 可净化值（round-trip 即独立副本，无需 deepcopy）；
            # 不可序列化键 fail-safe：不进基线、不进历史。
            stable_globals: dict[str, Any] = {}
            changes: list[dict[str, Any]] = []
            if isinstance(globals_, dict):
                for key, new_value in globals_.items():
                    new_stable = _stable_json(new_value)
                    if new_stable is _UNSTABLE:
                        continue
                    stable_globals[key] = new_stable
                    baseline = self._last_pause_globals
                    if baseline is None or key not in baseline:
                        if baseline is not None:
                            changes.append({"key": key, "old": None, "new": new_stable})
                    elif baseline[key] != new_stable:
                        changes.append({"key": key, "old": baseline[key], "new": new_stable})
            entry = {
                "seq": len(self.variable_history),
                "node_id": node_id,
                "reason": reason,
                "since_nodes": list(self._nodes_since_pause),
                "changes": changes,
            }
            self.variable_history.append(entry)
            if len(self.variable_history) > VARIABLE_HISTORY_LIMIT:
                del self.variable_history[:-VARIABLE_HISTORY_LIMIT]
            self._nodes_since_pause = []
            self._last_pause_globals = stable_globals

    def seed_baseline(self, globals_: Any) -> None:
        """resume 写回用户手动改写后调用：把改写同步进基线，不计为运行变化。"""
        with self._state:
            if isinstance(globals_, dict):
                baseline: dict[str, Any] = {}
                for key, value in globals_.items():
                    stable = _stable_json(value)
                    if stable is not _UNSTABLE:
                        baseline[key] = stable
                self._last_pause_globals = baseline

    def bump_hits(self, node_id: str) -> int:
        """命中一次断点，累计并返回该节点当前命中次数。"""
        hits = self.hit_counts.get(node_id, 0) + 1
        self.hit_counts[node_id] = hits
        return hits

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
                # docs/28 §3.1：截至本次暂停的变量变化历史（上限内全量、深拷贝）。
                "history": copy.deepcopy(self.variable_history),
            }


@dataclass
class DebuggerBroker:
    """全部调试会话；进程内单例，重启清空。"""

    _sessions: dict[str, DebugSession] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def create(
        self, *, graph_id: str, breakpoints: list[dict[str, Any]] | None
    ) -> DebugSession:
        table: dict[str, BreakpointSpec] = {}
        for bp in breakpoints or []:
            node_id = str(bp["node_id"])
            expression = bp.get("expression")
            expression = str(expression) if expression else None
            table[node_id] = BreakpointSpec(
                expression=expression,
                hit_count=bp.get("hitCount"),
                log_message=bp.get("logMessage"),
            )
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
