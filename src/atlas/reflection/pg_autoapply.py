"""进化候选门控自动生效记录的 PG 持久化（docs/132 §3.3，迁移 053；docs/126 §5 D75）。

与进程内 `AutoApplyStore`（ring 100）**方法签名一比一**：`next_run_id / add / get /
list / update / reset`，返回同一份投影 dict，故 run_autoapply/abort/REST 端点两档通用。

落库形状照 `reflection/pg_store.py`：id/seq 用全局 `storage_id_seq`、行内 tenant 过滤、
ring 淘汰用 `OFFSET :keep` 惰性 DELETE、reset 删本租户。run_id 为 `aa-N`，
数字部分即行 seq（`next_run_id` 与 `add` 两方法，编排先取号再落库）。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import Engine, text

from .autoapply import (
    AUTOAPPLY_RING_SIZE,
    AutoApplyRun,
    AutoApplyStore,  # noqa: F401  # re-export 方便 registry 联合导入（与 pg_shadow 先例对齐）
)


def _loads(value: Any, default: Any = None) -> Any:
    """JSONB 列经 psycopg 返回已是 dict/list，字符串则解（两档对拍不因驱动而异）。"""
    if value is None:
        return default
    if isinstance(value, str):
        return json.loads(value)
    return value


class PgAutoApplyStore:
    """AutoApplyRun PG 实现（每租户一实例，行内 tenant_id 过滤，非 RLS）。"""

    def __init__(self, engine: Engine, tenant_id: str) -> None:
        self._engine = engine
        self._tenant_id = tenant_id
        # next_run_id 取号后、add 落库前的 seq 暂存（按 id 隔离并发编排）。
        self._pending_seq: dict[str, int] = {}

    def next_run_id(self) -> str:
        with self._engine.begin() as db:
            seq = int(db.execute(text("SELECT nextval('storage_id_seq')")).scalar_one())
        run_id = f"aa-{seq}"
        self._pending_seq[run_id] = seq
        return run_id

    def add(self, run: AutoApplyRun) -> AutoApplyRun:
        seq = self._pending_seq.pop(run.run_id, None)
        with self._engine.begin() as db:
            if seq is None:
                # 防御：未经 next_run_id 直接落库（理论上编排不会），补取一号。
                seq = int(db.execute(text("SELECT nextval('storage_id_seq')")).scalar_one())
            db.execute(
                text(
                    "INSERT INTO autoapply_runs (tenant_id, run_id, candidate_id, graph_id, "
                    "base_version, enabled_scopes, patch, shadow, gate, publish_version, "
                    "rollout_status, status, reason, stages, created_at, updated_at) "
                    "VALUES (:tenant_id, :run_id, :candidate_id, :graph_id, :base_version, "
                    "CAST(:enabled_scopes AS JSONB), CAST(:patch AS JSONB), "
                    "CAST(:shadow AS JSONB), CAST(:gate AS JSONB), :publish_version, "
                    ":rollout_status, :status, :reason, CAST(:stages AS JSONB), "
                    ":created_at, :updated_at)"
                ),
                {
                    "tenant_id": self._tenant_id,
                    "run_id": run.run_id,
                    "candidate_id": run.candidate_id,
                    "graph_id": run.graph_id,
                    "base_version": run.base_version,
                    "enabled_scopes": json.dumps(run.enabled_scopes, ensure_ascii=False),
                    "patch": json.dumps(run.patch, ensure_ascii=False) if run.patch else None,
                    "shadow": json.dumps(run.shadow, ensure_ascii=False) if run.shadow else None,
                    "gate": json.dumps(run.gate, ensure_ascii=False) if run.gate else None,
                    "publish_version": run.publish_version,
                    "rollout_status": run.rollout_status,
                    "status": run.status,
                    "reason": run.reason,
                    "stages": json.dumps(run.stages, ensure_ascii=False),
                    "created_at": run.created_at,
                    "updated_at": run.updated_at,
                },
            )
            db.execute(
                text(
                    "DELETE FROM autoapply_runs WHERE tenant_id = :t AND run_id IN ("
                    "SELECT run_id FROM autoapply_runs WHERE tenant_id = :t "
                    "ORDER BY created_at DESC, run_id DESC OFFSET :keep)"
                ),
                {"t": self._tenant_id, "keep": AUTOAPPLY_RING_SIZE},
            )
        return run

    def get(self, run_id: str) -> dict[str, Any] | None:
        sql = (
            "SELECT run_id, candidate_id, graph_id, base_version, enabled_scopes, patch, "
            "shadow, gate, publish_version, rollout_status, status, reason, stages, "
            "created_at, updated_at FROM autoapply_runs "
            "WHERE tenant_id = :t AND run_id = :id"
        )
        with self._engine.connect() as db:
            row = db.execute(text(sql), {"t": self._tenant_id, "id": run_id}).first()
        if row is None:
            return None
        run = AutoApplyRun(
            run_id=row[0],
            candidate_id=row[1],
            graph_id=row[2],
            base_version=row[3],
            enabled_scopes=_loads(row[4], []),
            patch=_loads(row[5]),
            shadow=_loads(row[6]),
            gate=_loads(row[7]),
            publish_version=row[8],
            rollout_status=row[9],
            status=row[10],
            reason=row[11],
            stages=_loads(row[12], []),
            created_at=row[13],
            updated_at=row[14],
        )
        return run.model_dump()

    def list(self, graph_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """本租户自动生效记录倒序（可按图过滤；limit 1–200，端点层再 clamp）。"""
        bounded = max(1, min(int(limit), 200))
        clauses = ["tenant_id = :tenant_id"]
        args: dict[str, Any] = {"tenant_id": self._tenant_id, "limit": bounded}
        if graph_id is not None:
            clauses.append("graph_id = :graph_id")
            args["graph_id"] = graph_id
        sql = (
            "SELECT run_id, candidate_id, graph_id, base_version, enabled_scopes, patch, "
            "shadow, gate, publish_version, rollout_status, status, reason, stages, "
            "created_at, updated_at FROM autoapply_runs "
            f"WHERE {' AND '.join(clauses)} "
            "ORDER BY created_at DESC, run_id DESC LIMIT :limit"
        )
        with self._engine.connect() as db:
            rows = db.execute(text(sql), args).all()
        return [self._row_to_dict(row) for row in rows]

    def update(self, run: AutoApplyRun) -> AutoApplyRun:
        """整体替换（编排就地改 stages/status 后落库）。"""
        with self._engine.begin() as db:
            db.execute(
                text(
                    "UPDATE autoapply_runs SET enabled_scopes = CAST(:enabled_scopes AS JSONB), "
                    "patch = CAST(:patch AS JSONB), shadow = CAST(:shadow AS JSONB), "
                    "gate = CAST(:gate AS JSONB), publish_version = :publish_version, "
                    "rollout_status = :rollout_status, status = :status, reason = :reason, "
                    "stages = CAST(:stages AS JSONB), updated_at = :updated_at "
                    "WHERE tenant_id = :tenant_id AND run_id = :run_id"
                ),
                {
                    "tenant_id": self._tenant_id,
                    "run_id": run.run_id,
                    "enabled_scopes": json.dumps(run.enabled_scopes, ensure_ascii=False),
                    "patch": json.dumps(run.patch, ensure_ascii=False) if run.patch else None,
                    "shadow": json.dumps(run.shadow, ensure_ascii=False) if run.shadow else None,
                    "gate": json.dumps(run.gate, ensure_ascii=False) if run.gate else None,
                    "publish_version": run.publish_version,
                    "rollout_status": run.rollout_status,
                    "status": run.status,
                    "reason": run.reason,
                    "stages": json.dumps(run.stages, ensure_ascii=False),
                    "updated_at": run.updated_at,
                },
            )
        return run

    def reset(self) -> None:
        """/api/demo/reset 清空本租户表（其余资源照 registry 清单）。"""
        with self._engine.begin() as db:
            db.execute(
                text("DELETE FROM autoapply_runs WHERE tenant_id = :t"),
                {"t": self._tenant_id},
            )
        self._pending_seq.clear()

    @staticmethod
    def _row_to_dict(row: Any) -> dict[str, Any]:
        return {
            "run_id": row[0],
            "candidate_id": row[1],
            "graph_id": row[2],
            "base_version": row[3],
            "enabled_scopes": _loads(row[4], []),
            "patch": _loads(row[5]),
            "shadow": _loads(row[6]),
            "gate": _loads(row[7]),
            "publish_version": row[8],
            "rollout_status": row[9],
            "status": row[10],
            "reason": row[11],
            "stages": _loads(row[12], []),
            "created_at": row[13],
            "updated_at": row[14],
        }
