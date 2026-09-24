"""影子运行 PG 持久化（docs/61 §5，打包 H H4；D26 部分取回、不解除）。

与 `ShadowStore`（进程内 ring 100）同形：方法签名一比一、返回同一份
`ShadowRun.model_dump()`，故 REST 四端点与前端零改动。落库形状照
`recording/pg_reports.py`（id 用全局 `storage_id_seq`、行内 tenant 过滤、reset 清本租户），
ring 淘汰照 `message/deliveries.py` 的 `OFFSET :keep` DELETE 先例。

自动动作推断与对比一律复用 `shadow.py` 里的纯函数（单一事实源），本模块不自带第二套口径。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import Engine, text

from .shadow import (
    SHADOW_RING_SIZE,
    HumanOutcome,
    ShadowComparison,
    ShadowDecision,
    ShadowRun,
    ToolIntent,
    _now_iso,
    compare_shadow,
    infer_auto_action,
)

_COLS = (
    "id, graph_id, inputs, status, error, decisions, tool_intents, trace_id, "
    "auto_action, human_outcome, comparison, created_at"
)


def _loads(value: Any, default: Any = None) -> Any:
    """JSONB 列经 psycopg 返回已是 dict/list，字符串则解（两档对拍不因驱动而异）。"""
    if value is None:
        return default
    if isinstance(value, str):
        return json.loads(value)
    return value


class PgShadowStore:
    """影子运行 PG 实现（每租户一实例，行内 tenant_id 过滤，非 RLS）。"""

    def __init__(self, engine: Engine, tenant_id: str) -> None:
        self._engine = engine
        self._tenant_id = tenant_id

    def add(
        self,
        *,
        graph_id: str,
        trace_id: str,
        decisions: list[ShadowDecision],
        tool_intents: list[ToolIntent],
        inputs: dict[str, Any] | None = None,
        status: str = "completed",
        error: str | None = None,
        human_outcome: HumanOutcome | None = None,
    ) -> dict[str, Any]:
        """落一次影子运行；auto_action 与 comparison 与内存档同算同写。"""
        auto_action = infer_auto_action(tool_intents, decisions)
        run = ShadowRun(
            # 占位 id：seq 取自 storage_id_seq 后回填，保证 id 与 seq 数字部分一致（sr-N）。
            id="sr-pending",
            graph_id=graph_id,
            inputs=inputs,
            status=status,
            error=error,
            decisions=decisions,
            tool_intents=tool_intents,
            trace_id=trace_id,
            auto_action=auto_action,
            human_outcome=human_outcome,
            comparison=compare_shadow(auto_action, human_outcome),
            created_at=_now_iso(),
        )
        with self._engine.begin() as db:
            seq = int(db.execute(text("SELECT nextval('storage_id_seq')")).scalar_one())
            run = run.model_copy(update={"id": f"sr-{seq}"})
            db.execute(
                text(
                    "INSERT INTO shadow_runs (tenant_id, id, seq, graph_id, inputs, status, error, "
                    "decisions, tool_intents, trace_id, auto_action, human_outcome, comparison, "
                    "created_at) VALUES (:tenant_id, :id, :seq, :graph_id, :inputs, :status, :error, "
                    "CAST(:decisions AS JSONB), CAST(:tool_intents AS JSONB), :trace_id, :auto_action, "
                    "CAST(:human_outcome AS JSONB), CAST(:comparison AS JSONB), :created_at)"
                ),
                {
                    "tenant_id": self._tenant_id,
                    "id": run.id,
                    "seq": seq,
                    "graph_id": run.graph_id,
                    # inputs 可空：内存档把「无入参」存成 None，落成 '{}' 会让两档投影分叉。
                    "inputs": json.dumps(run.inputs, ensure_ascii=False) if run.inputs is not None else None,
                    "status": run.status,
                    "error": run.error,
                    "decisions": json.dumps(
                        [d.model_dump() for d in run.decisions], ensure_ascii=False
                    ),
                    "tool_intents": json.dumps(
                        [t.model_dump() for t in run.tool_intents], ensure_ascii=False
                    ),
                    "trace_id": run.trace_id,
                    "auto_action": run.auto_action,
                    "human_outcome": json.dumps(
                        run.human_outcome.model_dump() if run.human_outcome else None,
                        ensure_ascii=False,
                    ),
                    "comparison": json.dumps(
                        run.comparison.model_dump(), ensure_ascii=False
                    ),
                    "created_at": run.created_at,
                },
            )
            # 惰性 ring：删掉本租户超出最近 SHADOW_RING_SIZE 条的旧行（与内存 deque 对齐）。
            db.execute(
                text(
                    "DELETE FROM shadow_runs WHERE tenant_id = :t AND seq IN ("
                    "SELECT seq FROM shadow_runs WHERE tenant_id = :t "
                    "ORDER BY seq DESC OFFSET :keep)"
                ),
                {"t": self._tenant_id, "keep": SHADOW_RING_SIZE},
            )
        return run.model_dump()

    def get(self, sid: str) -> dict[str, Any] | None:
        sql = (
            f"SELECT {_COLS} FROM shadow_runs "
            "WHERE tenant_id = :t AND id = :id"
        )
        with self._engine.connect() as db:
            row = db.execute(text(sql), {"t": self._tenant_id, "id": sid}).first()
        return self._row_to_run(row).model_dump() if row is not None else None

    def list(self, graph_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """本租户影子记录倒序（可按图过滤；limit 1–200，端点层再 clamp）。"""
        bounded = max(1, min(int(limit), 200))
        clauses = ["tenant_id = :tenant_id"]
        args: dict[str, Any] = {"tenant_id": self._tenant_id, "limit": bounded}
        if graph_id is not None:
            clauses.append("graph_id = :graph_id")
            args["graph_id"] = graph_id
        sql = (
            f"SELECT {_COLS} FROM shadow_runs WHERE {' AND '.join(clauses)} "
            "ORDER BY seq DESC LIMIT :limit"
        )
        with self._engine.connect() as db:
            rows = db.execute(text(sql), args).all()
        return [self._row_to_run(row).model_dump() for row in rows]

    def attach_outcome(self, sid: str, outcome: HumanOutcome) -> dict[str, Any] | None:
        """补录/覆盖人工结果并重算 comparison；不存在返 None（API 层 404）。"""
        with self._engine.begin() as db:
            row = db.execute(
                text("SELECT auto_action FROM shadow_runs WHERE tenant_id = :t AND id = :id"),
                {"t": self._tenant_id, "id": sid},
            ).first()
            if row is None:
                return None
            comparison = compare_shadow(row[0], outcome)
            db.execute(
                text(
                    "UPDATE shadow_runs SET human_outcome = CAST(:outcome AS JSONB), "
                    "comparison = CAST(:comparison AS JSONB) "
                    "WHERE tenant_id = :t AND id = :id"
                ),
                {
                    "t": self._tenant_id,
                    "id": sid,
                    "outcome": json.dumps(outcome.model_dump(), ensure_ascii=False),
                    "comparison": json.dumps(comparison.model_dump(), ensure_ascii=False),
                },
            )
        return self.get(sid)

    def reset(self) -> None:
        """/api/demo/reset 清空本租户（其余资源照 registry 清单）。"""
        with self._engine.begin() as db:
            db.execute(
                text("DELETE FROM shadow_runs WHERE tenant_id = :t"),
                {"t": self._tenant_id},
            )

    @staticmethod
    def _row_to_run(row: Any) -> ShadowRun:
        """行 → ShadowRun（四子模型经 model_validate 回正，保证两档投影逐键一致）。"""
        return ShadowRun(
            id=row[0],
            graph_id=row[1],
            inputs=_loads(row[2]),
            status=row[3],
            error=row[4],
            decisions=[ShadowDecision.model_validate(d) for d in _loads(row[5], [])],
            tool_intents=[ToolIntent.model_validate(t) for t in _loads(row[6], [])],
            trace_id=row[7],
            auto_action=row[8],
            human_outcome=(
                HumanOutcome.model_validate(_loads(row[9])) if _loads(row[9]) is not None else None
            ),
            comparison=ShadowComparison.model_validate(
                _loads(row[10], ShadowComparison().model_dump())
            ),
            created_at=row[11],
        )
