# -*- coding: utf-8 -*-
"""A/B 实验的进程内 store（打包 BR＝D77，docs/134 §2.5；docs/127 里程碑 G）。

实验是配置资产（跨重启不可丢，同 BQ adapter_drafts 理由），两档方法签名一比一：
内存档进程内 dict ring 100 惰性裁（create 超限先裁最旧 concluded、仍超裁最旧插入）；
PG 档见 `PgExperimentStore`（迁移 056 `experiments` 表，id 走全局 `storage_id_seq`）。

并发口径：同图同时刻至多一个 running 实验由代码侧保证（BR-D6：API 创建前
`running_for_graph` 检查，冲突 409）；conclude 已终态抛 `ExperimentError`（照
RolloutError 先例 → REST 409）。
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any

from .experiment import Experiment, Verdict


class ExperimentError(ValueError):
    """实验状态机非法迁移（已终态重复 conclude）；REST 映射 409（照 RolloutError 先例）。"""


RING_SIZE = 100


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ExperimentStore:
    """进程内 per-tenant 实验档（随 TenantServices 装配；reset 清本租户）。"""

    def __init__(self) -> None:
        self._experiments: dict[str, Experiment] = {}
        self._counter = 0
        self._lock = threading.Lock()

    def create(
        self,
        *,
        graph_id: str,
        version_a: int,
        version_b: int,
        traffic_percent: int,
        primary_metric: str,
        min_samples: int,
        prior_config: Any = None,
        created_by: str = "",
    ) -> Experiment:
        """创建即 running（API 层已完成图/版本/rollout idle/互斥校验）。"""
        with self._lock:
            self._counter += 1
            exp = Experiment(
                id=f"exp-{self._counter}",
                graphId=graph_id,
                versionA=version_a,
                versionB=version_b,
                trafficPercent=traffic_percent,
                primaryMetric=primary_metric,  # type: ignore[arg-type]
                minSamples=min_samples,
                priorConfig=prior_config,
                createdBy=created_by,
                createdAt=_now_iso(),
            )
            self._experiments[exp.id] = exp
            self._trim_locked()
            return exp.model_copy(deep=True)

    def get(self, exp_id: str) -> Experiment | None:
        with self._lock:
            exp = self._experiments.get(exp_id)
        return exp.model_copy(deep=True) if exp is not None else None

    def list(self, *, graph_id: str | None = None) -> list[Experiment]:
        """按创建时间倒序；graph_id 过滤供 `GET /api/experiments?graphId=`。"""
        with self._lock:
            exps = [e.model_copy(deep=True) for e in self._experiments.values()]
        if graph_id is not None:
            exps = [e for e in exps if e.graphId == graph_id]
        exps.sort(key=lambda e: e.createdAt, reverse=True)
        return exps

    def running_for_graph(self, graph_id: str) -> Experiment | None:
        """BR-D6 互斥检查：同图至多一个 running（创建前调用，命中即 409）。"""
        with self._lock:
            for exp in self._experiments.values():
                if exp.graphId == graph_id and exp.status == "running":
                    return exp.model_copy(deep=True)
        return None

    def conclude(
        self,
        exp_id: str,
        *,
        verdict: Verdict,
        action: str,
        reason: str | None,
    ) -> Experiment | None:
        """冻结 verdict＋action＋concludedAt；不存在返 None（→404），已终态抛 ExperimentError（→409）。

        只落库判定结果；promote/rollback 与 priorConfig 恢复由 API 层编排（BR-D4/D5）。
        """
        with self._lock:
            exp = self._experiments.get(exp_id)
            if exp is None:
                return None
            if exp.status == "concluded":
                raise ExperimentError(f"实验 {exp_id} 已终态（concluded），不可重复 conclude")
            exp.status = "concluded"
            exp.verdict = verdict
            exp.conclusionAction = action  # type: ignore[assignment]
            exp.conclusionReason = reason
            exp.concludedAt = _now_iso()
            return exp.model_copy(deep=True)

    def reset(self) -> None:
        with self._lock:
            self._experiments.clear()
            self._counter = 0

    def _trim_locked(self) -> None:
        """ring 惰性裁：超限先裁最旧 concluded，仍超（全 running）裁最旧插入。"""
        while len(self._experiments) > RING_SIZE:
            concluded_ids = [
                e.id for e in self._experiments.values() if e.status == "concluded"
            ]
            if concluded_ids:
                del self._experiments[concluded_ids[0]]
            else:
                oldest = next(iter(self._experiments))
                del self._experiments[oldest]
