"""RoutingStore：每图 rollout 状态机 + 分流计数（M9，ADR T22③，契约 03 `rollout_config`、06 §6.17）。

进程内、每租户一个实例（挂 TenantServices，照 T20 TaskStore 先例；memory/PG 两分支均装配
进程内实现，PG 持久化随 11 S1 批次评估，沙盘不解除 D32）。

状态机（03 契约注释）：
  configure 只存配置不启动；
  start   idle → canary（取最新两发布版 stable=前一/candidate=最新，不足 2 个 409，未配置 409）；
  promote canary → full（唯一放量路径，仅手动，代码中不存在任何自动 promote）；
  rollback */ → rolled_back（candidate 撤流、stable 接全量；actor auto/manual 同一幂等函数）。

canary 期配置中的 full 段不生效（full 是 promote 后的运行态语义），求值时剔除；
full 状态短路全量 candidate；rolled_back 后新流量全走 stable（在途实例 pin 由 M5b/M6 承载）。
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from .models import RolloutConfig, RolloutStatus, TriggerEvent
from .router import (
    SEGMENT_FALLBACK,
    SEGMENT_FULL,
    SEGMENT_STABLE,
    SEGMENT_UNPUBLISHED,
    resolve_version,
)

_SEGMENT_KEYS = ("internal", "lowValueBucket", "canary", "full", "fallback")


class RolloutError(ValueError):
    """状态机非法迁移/前置不满足（REST 层一律映射 409）；配置非法是 pydantic ValidationError→422。"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _empty_traffic() -> dict[str, Any]:
    return {"stable": 0, "candidate": 0, "segments": {key: 0 for key in _SEGMENT_KEYS}}


class RolloutState(BaseModel):
    """单图 rollout 运行态（GET 投影主体；config 为 PUT 存入的配置，未配置为 None）。"""

    graph_id: str
    status: RolloutStatus = "idle"
    config: RolloutConfig | None = None
    stable: int | None = None
    candidate: int | None = None
    started_at: str | None = None
    rolled_back_at: str | None = None
    rollback_reason: str | None = None
    rollback_actor: str | None = None  # 超集：最后一次回滚的 actor（auto/manual），便于前端与告警区分
    traffic: dict[str, Any] = Field(default_factory=_empty_traffic)


class RoutingStore:
    def __init__(self) -> None:
        self._states: dict[str, RolloutState] = {}
        self._lock = threading.Lock()

    # ---------- 配置 ----------

    def configure(self, graph_id: str, config: RolloutConfig) -> RolloutState:
        """存灰度配置（只存不启动）；配置合法性由 RolloutConfig/pydantic 在构造期保证。"""
        with self._lock:
            state = self._states.get(graph_id)
            if state is None:
                state = RolloutState(graph_id=graph_id)
                self._states[graph_id] = state
            state.config = config
            return state.model_copy(deep=True)

    def snapshot(self, graph_id: str) -> RolloutState:
        """GET 投影（深拷贝防外部改写）；从未配置的图返 idle 默认态。"""
        with self._lock:
            state = self._states.get(graph_id)
            if state is None:
                return RolloutState(graph_id=graph_id)
            return state.model_copy(deep=True)

    # ---------- 状态机 ----------

    def start(self, graph_id: str, versions: list[int]) -> RolloutState:
        """idle→canary：stable/candidate 取最新两个发布版（versions 升序）。"""
        with self._lock:
            state = self._require(graph_id)
            if state.config is None:
                raise RolloutError("尚未配置灰度规则，无法启动 canary（先 PUT rollout 配置）")
            if state.status != "idle":
                raise RolloutError(f"当前状态 {state.status} 不可启动 canary（仅 idle 可启动）")
            if len(versions) < 2:
                raise RolloutError("发布版本不足 2 个：canary 需要 stable 与 candidate 两个发布版")
            state.stable = versions[-2]
            state.candidate = versions[-1]
            state.status = "canary"
            state.started_at = _now_iso()
            state.rolled_back_at = None
            state.rollback_reason = None
            state.rollback_actor = None
            state.traffic = _empty_traffic()
            return state.model_copy(deep=True)

    def promote(self, graph_id: str) -> RolloutState:
        """canary→full：唯一放量路径，仅手动。"""
        with self._lock:
            state = self._require(graph_id)
            if state.status != "canary":
                raise RolloutError(f"当前状态 {state.status} 不可放量（仅 canary 可 promote 到 full）")
            state.status = "full"
            return state.model_copy(deep=True)

    def rollback(
        self, graph_id: str, *, actor: str = "manual", reason: str = ""
    ) -> RolloutState:
        """任意态→rolled_back，幂等：重复回滚不报错、不覆盖首次原因；idle 未启动 409。"""
        with self._lock:
            state = self._states.get(graph_id)
            if state is None or state.candidate is None or state.status == "idle":
                raise RolloutError("该图尚未启动灰度，无可回滚的 candidate")
            if state.status == "rolled_back":
                return state.model_copy(deep=True)  # 幂等：返现状
            state.status = "rolled_back"
            state.rolled_back_at = _now_iso()
            state.rollback_reason = reason or "manual rollback"
            state.rollback_actor = actor
            return state.model_copy(deep=True)

    # ---------- 路由 ----------

    def resolve(
        self, graph_id: str, *, tenant: str, event: TriggerEvent
    ) -> tuple[int | None, str]:
        """按当前运行态解析版本并累计分流计数；返 (releaseVersion, segment)。

        无 entry/未配置/未启动且无任何版本信息 → (None, "unpublished")，由 API 层转 409。
        """
        with self._lock:
            state = self._states.get(graph_id)
            if state is None:
                return None, SEGMENT_UNPUBLISHED
            if state.status == "full":
                version, segment = state.candidate, SEGMENT_FULL
            elif state.status == "rolled_back":
                version, segment = state.stable, SEGMENT_STABLE
            elif state.status == "canary":
                config = state.config
                if config is not None:
                    # canary 期 full 段不生效（promote 后才全量）
                    config = config.model_copy(
                        update={
                            "rules": [rule for rule in config.rules if rule.to != "full"]  # type: ignore[attr-defined]
                        }
                    )
                version, segment = resolve_version(
                    graph_id=graph_id,
                    stable=state.stable,
                    candidate=state.candidate,
                    tenant=tenant,
                    event=event,
                    config=config,
                )
            else:  # idle：未启动，无 candidate
                version, segment = resolve_version(
                    graph_id=graph_id,
                    stable=state.stable,
                    candidate=state.candidate,
                    tenant=tenant,
                    event=event,
                    config=state.config,
                )
                if version is None:
                    segment = SEGMENT_UNPUBLISHED
            self._count(state, version, segment)
            return version, segment

    @staticmethod
    def _count(state: RolloutState, version: int | None, segment: str) -> None:
        if version is not None and version == state.candidate and state.candidate is not None:
            state.traffic["candidate"] += 1
        elif version is not None:
            state.traffic["stable"] += 1
        if segment in state.traffic["segments"]:
            state.traffic["segments"][segment] += 1

    # ---------- 管理 ----------

    def _require(self, graph_id: str) -> RolloutState:
        state = self._states.get(graph_id)
        if state is None:
            raise RolloutError("尚未配置灰度规则（先 PUT rollout 配置）")
        return state

    def reset(self) -> None:
        with self._lock:
            self._states.clear()
