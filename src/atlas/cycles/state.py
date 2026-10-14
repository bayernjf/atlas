# -*- coding: utf-8 -*-
"""圈会话状态（docs/130 打包 BN＝D69；AU 里程碑 C 形状权威 §3.2 的 C-D6/C-D8）。

**一圈＝一次真实 `run_graph`**（不是再套一层 StateGraph）：这样每圈都继承既有语义——
审批/事件等待/子图/断点/录制/监控/回放/影子，一样不少；`engine/` 的 W1 占位骨架一行不改
（docs/88 §3 P-2(a) 冻结继续有效）。

本模块只做**状态与判定原语**，零 IO、零 litellm、零 loader 依赖：

- `CycleSession`：一条圈会话（一次"自走循环"）的序号、圈 runs、累计 token、墙钟与状态；
- `budget_exhausted(...)`：C-D3 的**三道预算闸门**判定（纯函数，任一触顶即返回该闸门名）；
- `CycleRegistry`：进程内注册表（**单副本语义**，照 docs/62 三道闸；有界 ring 200）。

**为什么不落库**：`runs` 两列（迁移 050，`cycle_seq`／`cycle_group_id`）已经能回答
"这一圈跑到第几圈、属于哪条会话"；圈会话的**活体状态**（累计 token/墙钟/是否被停）只在
本进程内有意义，而单副本约束下"重启即结束圈、不自动续跑"正是既有纪律（D44 的同款取舍）。
"""
from __future__ import annotations

import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

#: 进程内会话 ring 容量（照 monitoring ring 200／usage ring 2000 先例；超出淘汰最旧会话）。
CYCLE_SESSION_RING = 200

CycleState = Literal[
    "running", "suspended", "completed", "budget_exhausted", "stopped", "failed"
]

#: 三道闸门名（与 `GraphDSL.loop` 的三个 max* 形参一一对应；""＝未触顶）。
GATE_ITERATIONS = "maxIterations"
GATE_WALL_CLOCK = "maxWallClockSeconds"
GATE_TOKENS = "maxTokens"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class CycleRunRef:
    """圈里的一次真实运行（`run_id` 与 `runs` 表主键同值）。"""

    run_id: str
    seq: int
    status: str = ""
    total_tokens: int = 0

    def public_view(self) -> dict:
        return {
            "runId": self.run_id,
            "seq": self.seq,
            "status": self.status,
            "totalTokens": self.total_tokens,
        }


@dataclass
class CycleSession:
    """一条圈会话：一次触发引发的、自走循环的全过程账。"""

    group_id: str
    tenant_id: str
    graph_id: str
    max_iterations: int = 10
    max_wall_clock_seconds: int = 1800
    max_tokens: int = 200000
    seq: int = 1
    runs: list[CycleRunRef] = field(default_factory=list)
    tokens_spent: int = 0
    state: CycleState = "running"
    stop_reason: str = ""
    started_at: str = field(default_factory=_now_iso)
    started_monotonic: float = field(default_factory=time.monotonic)

    def record_run(self, run_id: str, *, seq: int | None = None,
                   status: str = "", total_tokens: int = 0) -> CycleRunRef:
        """记一次圈运行并累加 token（同一 run_id 重复登记按覆盖处理，幂等）。"""
        ref = CycleRunRef(
            run_id=run_id,
            seq=self.seq if seq is None else seq,
            status=status,
            total_tokens=total_tokens,
        )
        for index, existing in enumerate(self.runs):
            if existing.run_id == run_id:
                self.runs[index] = ref
                break
        else:
            self.runs.append(ref)
        self.tokens_spent += total_tokens
        self.seq = ref.seq
        return ref

    def elapsed_seconds(self, *, now_monotonic: float | None = None) -> float:
        """墙钟秒数（单调钟，回放/重启不影响；进程重启后会话本身已消失，见模块 docstring）。"""
        end = time.monotonic() if now_monotonic is None else now_monotonic
        return max(0.0, end - self.started_monotonic)

    def is_stopped(self) -> bool:
        """是否已被"停整条圈"标记（supervisor 开下一圈前必须查——反向门 U1440 守这条）。"""
        return self.state == "stopped"

    def is_terminal(self) -> bool:
        """终态＝不会再开新圈（stopped 也算：人已经叫停了整条会话）。"""
        return self.state in ("completed", "budget_exhausted", "stopped", "failed")

    def public_view(self, *, now_monotonic: float | None = None) -> dict:
        """REST 投影（`GET /api/runs/{run_id}/cycle` 的形状，docs/130 §3.4）。"""
        return {
            "groupId": self.group_id,
            "tenantId": self.tenant_id,
            "graphId": self.graph_id,
            "seq": self.seq,
            "state": self.state,
            "stopReason": self.stop_reason,
            "tokensSpent": self.tokens_spent,
            "elapsedSeconds": round(self.elapsed_seconds(now_monotonic=now_monotonic), 1),
            "maxIterations": self.max_iterations,
            "maxWallClockSeconds": self.max_wall_clock_seconds,
            "maxTokens": self.max_tokens,
            "startedAt": self.started_at,
            "runs": [ref.public_view() for ref in self.runs],
        }


def budget_exhausted(
    session: CycleSession,
    *,
    tokens_spent: int | None = None,
    now_monotonic: float | None = None,
) -> str:
    """C-D3 三道闸门判定：任一触顶即返回该闸门名（迭代 → 墙钟 → token 的固定次序）。

    - 迭代数：`seq` 已达 `maxIterations`（**跑完当前圈才判**，不在圈内打断）；
    - 墙钟：自 `started_monotonic` 起的秒数 ≥ `maxWallClockSeconds`；
    - token：本圈结束后累计 `tokens_spent` ≥ `maxTokens`。

    `tokens_spent` 缺省读会话累计值（无用量记录按 0 计，不报错——AU-D5 的口径）。
    """
    if session.seq >= session.max_iterations:
        return GATE_ITERATIONS
    if session.elapsed_seconds(now_monotonic=now_monotonic) >= session.max_wall_clock_seconds:
        return GATE_WALL_CLOCK
    spent = session.tokens_spent if tokens_spent is None else tokens_spent
    if spent >= session.max_tokens:
        return GATE_TOKENS
    return ""


class CycleRegistry:
    """进程内圈会话注册表（单副本；有界 ring 200；`reset` 供 `/api/demo/reset` 调用）。

    **索引**：`group_id → session`（有序 dict 实现 ring 淘汰）＋ `run_id → group_id`
    （供 REST 由 run 反查会话；随 ring 淘汰同步剪枝，不留悬挂指针）。
    """

    def __init__(self, ring_size: int = CYCLE_SESSION_RING) -> None:
        self._lock = threading.Lock()
        self._sessions: OrderedDict[str, CycleSession] = OrderedDict()
        self._by_run: dict[str, str] = {}
        self._ring_size = ring_size

    # --- 写 ---------------------------------------------------------------

    def create(
        self,
        *,
        tenant_id: str,
        graph_id: str,
        max_iterations: int,
        max_wall_clock_seconds: int,
        max_tokens: int,
        group_id: str | None = None,
    ) -> CycleSession:
        """新建圈会话（第一圈开始前调用一次；`group_id` 缺省生成 `cyc-N`）。"""
        with self._lock:
            if group_id is None:
                group_id = f"cyc-{uuid.uuid4().hex[:12]}"
            session = CycleSession(
                group_id=group_id,
                tenant_id=tenant_id,
                graph_id=graph_id,
                max_iterations=max_iterations,
                max_wall_clock_seconds=max_wall_clock_seconds,
                max_tokens=max_tokens,
            )
            self._sessions[group_id] = session
            self._sessions.move_to_end(group_id)
            self._evict_locked()
            return session

    def get(self, group_id: str) -> CycleSession | None:
        with self._lock:
            return self._sessions.get(group_id)

    def stop(self, group_id: str) -> CycleSession | None:
        """停整条圈会话（C-D8）：置 `stopped`；已终态原样返回（幂等 200）。"""
        with self._lock:
            session = self._sessions.get(group_id)
            if session is None:
                return None
            if not session.is_terminal():
                session.state = "stopped"
                session.stop_reason = "cycle_stopped"
            return session

    def finish(self, group_id: str, *, state: CycleState, stop_reason: str = "") -> None:
        """圈终态收口（completed／budget_exhausted／suspended／failed）。"""
        with self._lock:
            session = self._sessions.get(group_id)
            if session is None:
                return
            session.state = state
            session.stop_reason = stop_reason or session.stop_reason

    def reset(self, tenant_id: str | None = None) -> None:
        """`/api/demo/reset`：清本租户（或全部）圈会话与索引。"""
        with self._lock:
            if tenant_id is None:
                self._sessions.clear()
                self._by_run.clear()
                return
            for group_id, session in list(self._sessions.items()):
                if session.tenant_id == tenant_id:
                    self._sessions.pop(group_id, None)
                    for run_id, mapped in list(self._by_run.items()):
                        if mapped == group_id:
                            self._by_run.pop(run_id, None)

    # --- 读 ---------------------------------------------------------------

    def find_by_run(self, run_id: str) -> CycleSession | None:
        """由 runId 反查会话（非圈 run ⇒ None，REST 层据此 404）。"""
        with self._lock:
            group_id = self._by_run.get(run_id)
            return self._sessions.get(group_id) if group_id else None

    def bind_run(self, run_id: str, group_id: str) -> None:
        with self._lock:
            self._by_run[run_id] = group_id

    def list_tenant(self, tenant_id: str) -> list[CycleSession]:
        with self._lock:
            return [s for s in self._sessions.values() if s.tenant_id == tenant_id]

    # --- 内部 -------------------------------------------------------------

    def _evict_locked(self) -> None:
        while len(self._sessions) > self._ring_size:
            group_id, _ = self._sessions.popitem(last=False)
            for run_id, mapped in list(self._by_run.items()):
                if mapped == group_id:
                    self._by_run.pop(run_id, None)


#: 进程级单例（照 `monitoring`／`llm.config` 的单例模式；单副本语义）。
_REGISTRY: CycleRegistry | None = None
_REGISTRY_LOCK = threading.Lock()


def cycle_registry() -> CycleRegistry:
    """进程级圈会话注册表单例。"""
    global _REGISTRY
    if _REGISTRY is None:
        with _REGISTRY_LOCK:
            if _REGISTRY is None:
                _REGISTRY = CycleRegistry()
    return _REGISTRY
