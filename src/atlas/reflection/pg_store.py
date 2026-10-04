"""反思候选与收尾报告的 PG 持久化（打包 ZU，docs/94 §3.1／E-5/E-6；docs/88 D-6 触发）。

与进程内 `ReflectionStore`（双 deque ring 100）**方法签名一比一**：
`next_candidate_id / add_candidate / add_report / list_reports / get_candidate /
record_decision / reset`，返回同一份投影 dict，故 run_pass、REST 两端点与前端零改动。

落库形状照 `recording/pg_shadow.py`：id/seq 用全局 `storage_id_seq`、行内 tenant 过滤、
ring 淘汰用 `OFFSET :keep` 惰性 DELETE、reset 删本租户。两点与 shadow 不同：
- reports 无业务 id，seq 仅用于排序、**不进投影**；candidates 的 id 为 `refl-N`，
  其数字部分即该行 seq。`next_candidate_id()` 与 `add_candidate()` 是两个方法
  （run_pass 先取号再落库），故取号时把 seq 暂存在实例内、落库时按 id 取出。
- changes 落库用 by_alias 形状（`from`/`to`，与内存档 `get_candidate` 投影一致）；
  `Change` 开了 populate_by_name，读回 model_validate 同样能吃 `from`。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import Engine, text

from .candidate import (
    CANDIDATE_RING_SIZE,
    REPORT_RING_SIZE,
    Change,
    DecisionStatus,
    ReflectionCandidate,
    ReflectionReport,
    ReflectionStore,  # noqa: F401  # re-export 方便 registry 联合导入（与 pg_shadow 先例对齐）
    _now_iso,
)


def _loads(value: Any, default: Any = None) -> Any:
    """JSONB 列经 psycopg 返回已是 dict/list，字符串则解（两档对拍不因驱动而异）。"""
    if value is None:
        return default
    if isinstance(value, str):
        return json.loads(value)
    return value


class PgReflectionStore:
    """反思报告/候选 PG 实现（每租户一实例，行内 tenant_id 过滤，非 RLS）。"""

    def __init__(self, engine: Engine, tenant_id: str) -> None:
        self._engine = engine
        self._tenant_id = tenant_id
        # next_candidate_id 取号后、add_candidate 落库前的 seq 暂存（按 id 隔离并发 pass）。
        self._pending_seq: dict[str, int] = {}

    def next_candidate_id(self) -> str:
        with self._engine.begin() as db:
            seq = int(db.execute(text("SELECT nextval('storage_id_seq')")).scalar_one())
        candidate_id = f"refl-{seq}"
        self._pending_seq[candidate_id] = seq
        return candidate_id

    def add_candidate(self, candidate: ReflectionCandidate) -> ReflectionCandidate:
        seq = self._pending_seq.pop(candidate.candidate_id, None)
        with self._engine.begin() as db:
            if seq is None:
                # 防御：未经 next_candidate_id 直接落库（理论上 run_pass 不会），补取一号。
                seq = int(db.execute(text("SELECT nextval('storage_id_seq')")).scalar_one())
            by_alias = candidate.model_dump(by_alias=True)
            db.execute(
                text(
                    "INSERT INTO reflection_candidates (tenant_id, id, seq, graph_id, base_version, "
                    "changes, prompt_suggestions, evidence_digest, generated_at, decision_status, "
                    "decided_at) VALUES (:tenant_id, :id, :seq, :graph_id, :base_version, "
                    "CAST(:changes AS JSONB), CAST(:suggestions AS JSONB), :evidence_digest, "
                    ":generated_at, :decision_status, :decided_at)"
                ),
                {
                    "tenant_id": self._tenant_id,
                    "id": candidate.candidate_id,
                    "seq": seq,
                    "graph_id": candidate.graph_id,
                    "base_version": candidate.base_version,
                    "changes": json.dumps(by_alias["changes"], ensure_ascii=False),
                    "suggestions": json.dumps(
                        candidate.prompt_suggestions, ensure_ascii=False
                    ),
                    "evidence_digest": candidate.evidence_digest,
                    "generated_at": candidate.generated_at,
                    "decision_status": candidate.decision_status,
                    "decided_at": candidate.decided_at,
                },
            )
            db.execute(
                text(
                    "DELETE FROM reflection_candidates WHERE tenant_id = :t AND id IN ("
                    "SELECT id FROM reflection_candidates WHERE tenant_id = :t "
                    "ORDER BY seq DESC OFFSET :keep)"
                ),
                {"t": self._tenant_id, "keep": CANDIDATE_RING_SIZE},
            )
        return candidate

    def add_report(self, report: ReflectionReport) -> ReflectionReport:
        with self._engine.begin() as db:
            seq = int(db.execute(text("SELECT nextval('storage_id_seq')")).scalar_one())
            db.execute(
                text(
                    "INSERT INTO reflection_reports (tenant_id, seq, candidate_id, graph_id, "
                    "base_version, status, reasons, generated_at) VALUES (:tenant_id, :seq, "
                    ":candidate_id, :graph_id, :base_version, :status, CAST(:reasons AS JSONB), "
                    ":generated_at)"
                ),
                {
                    "tenant_id": self._tenant_id,
                    "seq": seq,
                    "candidate_id": report.candidate_id,
                    "graph_id": report.graph_id,
                    "base_version": report.base_version,
                    "status": report.status,
                    "reasons": json.dumps(report.reasons, ensure_ascii=False),
                    "generated_at": report.generated_at,
                },
            )
            db.execute(
                text(
                    "DELETE FROM reflection_reports WHERE tenant_id = :t AND seq IN ("
                    "SELECT seq FROM reflection_reports WHERE tenant_id = :t "
                    "ORDER BY seq DESC OFFSET :keep)"
                ),
                {"t": self._tenant_id, "keep": REPORT_RING_SIZE},
            )
        return report

    def list_reports(self, graph_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """本租户收尾记录倒序（可按图过滤）；candidate_id 非空者 LEFT JOIN 附当前决策态。"""
        bounded = max(1, min(int(limit), 200))
        clauses = ["r.tenant_id = :tenant_id"]
        args: dict[str, Any] = {"tenant_id": self._tenant_id, "limit": bounded}
        if graph_id is not None:
            clauses.append("r.graph_id = :graph_id")
            args["graph_id"] = graph_id
        sql = (
            "SELECT r.candidate_id, r.graph_id, r.base_version, r.status, r.reasons, "
            "r.generated_at, c.decision_status "
            "FROM reflection_reports r "
            "LEFT JOIN reflection_candidates c "
            "ON c.tenant_id = r.tenant_id AND c.id = r.candidate_id "
            f"WHERE {' AND '.join(clauses)} ORDER BY r.seq DESC LIMIT :limit"
        )
        with self._engine.connect() as db:
            rows = db.execute(text(sql), args).all()
        return [
            {
                "candidate_id": row[0],
                "graph_id": row[1],
                "base_version": row[2],
                "status": row[3],
                "reasons": _loads(row[4], []),
                "generated_at": row[5],
                "decision_status": row[6],
            }
            for row in rows
        ]

    def get_candidate(self, candidate_id: str) -> dict[str, Any] | None:
        sql = (
            "SELECT id, graph_id, base_version, changes, prompt_suggestions, evidence_digest, "
            "generated_at, decision_status, decided_at FROM reflection_candidates "
            "WHERE tenant_id = :t AND id = :id"
        )
        with self._engine.connect() as db:
            row = db.execute(text(sql), {"t": self._tenant_id, "id": candidate_id}).first()
        if row is None:
            return None
        candidate = ReflectionCandidate(
            candidate_id=row[0],
            graph_id=row[1],
            base_version=row[2],
            changes=[Change.model_validate(c) for c in _loads(row[3], [])],
            prompt_suggestions=_loads(row[4], []),
            evidence_digest=row[5],
            generated_at=row[6],
            decision_status=row[7],
            decided_at=row[8],
        )
        return candidate.model_dump(by_alias=True)

    def record_decision(
        self,
        candidate_id: str,
        status: DecisionStatus,
        decided_at: str | None = None,
    ) -> bool:
        """登记/改判候选处理标记；候选不存在（含跨租户）返 False（API 层 404）。"""
        stamp = decided_at or _now_iso()
        with self._engine.begin() as db:
            result = db.execute(
                text(
                    "UPDATE reflection_candidates SET decision_status = :status, decided_at = :stamp "
                    "WHERE tenant_id = :t AND id = :id"
                ),
                {
                    "status": status,
                    "stamp": stamp,
                    "t": self._tenant_id,
                    "id": candidate_id,
                },
            )
            return result.rowcount > 0

    def reset(self) -> None:
        """/api/demo/reset 清空本租户两表（其余资源照 registry 清单）。"""
        with self._engine.begin() as db:
            db.execute(
                text("DELETE FROM reflection_reports WHERE tenant_id = :t"),
                {"t": self._tenant_id},
            )
            db.execute(
                text("DELETE FROM reflection_candidates WHERE tenant_id = :t"),
                {"t": self._tenant_id},
            )
        self._pending_seq.clear()
