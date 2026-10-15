# -*- coding: utf-8 -*-
"""A/B 实验的 PG 持久化（docs/134 §2.5，迁移 056；与进程内 `ExperimentStore` 签名一比一）。

id 数字部分取全局 `storage_id_seq`（adapter_drafts/autoapply_runs 同款）；行内 tenant_id
过滤；prior_config/verdict JSONB（CAST 写入、`_loads` 兼容读回）；时间列 TEXT ISO-8601
（031 同款口径：读回投影与内存档逐键同型，无 datetime 转换漂移）；conclude 走
`SELECT ... FOR UPDATE` 行锁内状态检查＋UPDATE（pg_store 同款串行口径）；reset 删本租户。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import Engine, text

from .experiment import Experiment, Verdict
from .experiment_store import ExperimentError  # noqa: F401  # re-export 对齐 pg_drafts 先例
from .models import RolloutConfig


def _loads(value: Any, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, str):
        return json.loads(value)
    return value


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


_COLUMNS = (
    "id, tenant_id, graph_id, version_a, version_b, traffic_percent, primary_metric, "
    "min_samples, status, prior_config, verdict, conclusion_action, conclusion_reason, "
    "created_by, created_at, concluded_at"
)


class PgExperimentStore:
    def __init__(self, engine: Engine, tenant_id: str) -> None:
        self._engine = engine
        self._tenant_id = tenant_id

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
        """单步取号＋落库（创建是一个动作，照 PgAdapterDraftStore.add 先例）。"""
        now = _now_iso()
        with self._engine.begin() as db:
            seq = int(db.execute(text("SELECT nextval('storage_id_seq')")).scalar_one())
            exp_id = f"exp-{seq}"
            db.execute(
                text(
                    "INSERT INTO experiments (id, tenant_id, graph_id, version_a, version_b, "
                    "traffic_percent, primary_metric, min_samples, status, prior_config, "
                    "verdict, conclusion_action, conclusion_reason, created_by, created_at, "
                    "concluded_at) VALUES (:id, :t, :g, :va, :vb, :tp, :pm, :ms, 'running', "
                    "CAST(:pc AS JSONB), NULL, NULL, NULL, :cb, :now, NULL)"
                ),
                {
                    "id": exp_id,
                    "t": self._tenant_id,
                    "g": graph_id,
                    "va": version_a,
                    "vb": version_b,
                    "tp": traffic_percent,
                    "pm": primary_metric,
                    "ms": min_samples,
                    "pc": (
                        json.dumps(prior_config.model_dump(), ensure_ascii=False)
                        if prior_config is not None
                        else None
                    ),
                    "cb": created_by,
                    "now": now,
                },
            )
        exp = self.get(exp_id)
        assert exp is not None  # 同事务刚插入
        return exp

    def get(self, exp_id: str) -> Experiment | None:
        sql = f"SELECT {_COLUMNS} FROM experiments WHERE tenant_id = :t AND id = :id"
        with self._engine.connect() as db:
            row = db.execute(text(sql), {"t": self._tenant_id, "id": exp_id}).first()
        return self._row_to_experiment(row) if row is not None else None

    def list(self, *, graph_id: str | None = None) -> list[Experiment]:
        clauses = ["tenant_id = :t"]
        args: dict[str, Any] = {"t": self._tenant_id}
        if graph_id is not None:
            clauses.append("graph_id = :g")
            args["g"] = graph_id
        sql = (
            f"SELECT {_COLUMNS} FROM experiments WHERE {' AND '.join(clauses)} "
            "ORDER BY created_at DESC, id DESC"
        )
        with self._engine.connect() as db:
            rows = db.execute(text(sql), args).all()
        return [self._row_to_experiment(row) for row in rows]

    def running_for_graph(self, graph_id: str) -> Experiment | None:
        sql = (
            f"SELECT {_COLUMNS} FROM experiments "
            "WHERE tenant_id = :t AND graph_id = :g AND status = 'running' "
            "ORDER BY created_at DESC, id DESC LIMIT 1"
        )
        with self._engine.connect() as db:
            row = db.execute(text(sql), {"t": self._tenant_id, "g": graph_id}).first()
        return self._row_to_experiment(row) if row is not None else None

    def conclude(
        self,
        exp_id: str,
        *,
        verdict: Verdict,
        action: str,
        reason: str | None,
    ) -> Experiment | None:
        """行锁内状态检查＋冻结；不存在返 None（→404），已终态抛 ExperimentError（→409）。"""
        now = _now_iso()
        with self._engine.begin() as db:
            row = db.execute(
                text(
                    "SELECT status FROM experiments WHERE tenant_id = :t AND id = :id "
                    "FOR UPDATE"
                ),
                {"t": self._tenant_id, "id": exp_id},
            ).first()
            if row is None:
                return None
            if row[0] == "concluded":
                raise ExperimentError(f"实验 {exp_id} 已终态（concluded），不可重复 conclude")
            db.execute(
                text(
                    "UPDATE experiments SET status = 'concluded', "
                    "verdict = CAST(:v AS JSONB), conclusion_action = :a, "
                    "conclusion_reason = :r, concluded_at = :now "
                    "WHERE tenant_id = :t AND id = :id"
                ),
                {
                    "t": self._tenant_id,
                    "id": exp_id,
                    "v": json.dumps(verdict.model_dump(), ensure_ascii=False),
                    "a": action,
                    "r": reason,
                    "now": now,
                },
            )
        return self.get(exp_id)

    def reset(self) -> None:
        with self._engine.begin() as db:
            db.execute(
                text("DELETE FROM experiments WHERE tenant_id = :t"),
                {"t": self._tenant_id},
            )

    @staticmethod
    def _row_to_experiment(row: Any) -> Experiment:
        prior = _loads(row[9])
        verdict = _loads(row[10])
        return Experiment(
            id=row[0],
            graphId=row[2],
            versionA=row[3],
            versionB=row[4],
            trafficPercent=row[5],
            primaryMetric=row[6],
            minSamples=row[7],
            status=row[8],
            priorConfig=RolloutConfig(**prior) if isinstance(prior, dict) else None,
            verdict=Verdict(**verdict) if isinstance(verdict, dict) else None,
            conclusionAction=row[11],
            conclusionReason=row[12],
            createdBy=row[13],
            createdAt=row[14],
            concludedAt=row[15],
        )
