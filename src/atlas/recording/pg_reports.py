"""批量回放发布报告的 PG 沉淀实现（docs/56 §2，迁移 022）。

与 ``recording.reports.ReportStore`` **方法形状完全一致**（record / list_summary /
list_all_summary / get / reset），API 层零改动即可在 PG 档替换进程内 ring：

- id 用全局 ``storage_id_seq``（rr-N），数字部分另存 seq 供 (租户, 图, seq) 排序；
- 摘要列表 SELECT 不取 cases（逐例详情仅 get 取）；
- 行内 tenant_id 过滤，不属于该图的 get 返 None（API 404，跨租户不泄漏）；
- PG 为持久化档，不做 ring 淘汰（列表端点本就 limit/clamp）；
- 报告是运行产物，``/api/demo/reset`` 删本租户报告（录制用例仍不清）。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Engine, text

from .reports import ReleaseReport, ReportCaseRow, ReportTrigger

# 摘要列（不含 cases 大字段）；详情列额外取 cases。
_SUMMARY_COLS = (
    "id, graph_id, target, trigger, total, passed, failed, skipped, "
    "blocked, pass_rate, created_at"
)
_DETAIL_COLS = _SUMMARY_COLS + ", cases"


def _jsonb(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


class PgReportStore:
    """发布报告 PG 存储（每租户一个实例，挂 TenantServices）。"""

    def __init__(self, engine: Engine, tenant_id: str) -> None:
        self._engine = engine
        self._tenant_id = tenant_id

    @staticmethod
    def _row_to_report(row: Any, *, with_cases: bool) -> ReleaseReport:
        # 摘要列序：0 id,1 graph_id,2 target,3 trigger,4 total,5 passed,6 failed,
        # 7 skipped,8 blocked,9 pass_rate,10 created_at；详情行 11 cases。
        cases: list[ReportCaseRow] = []
        if with_cases:
            cases = [ReportCaseRow(**item) for item in (_jsonb(row[11]) or [])]
        return ReleaseReport(
            id=row[0],
            graph_id=row[1],
            target=row[2],
            trigger=row[3],
            total=row[4],
            passed=row[5],
            failed=row[6],
            skipped=row[7],
            blocked=row[8],
            pass_rate=row[9],
            cases=cases,
            created_at=row[10],
        )

    def record(
        self, *, graph_id: str, trigger: ReportTrigger, report: dict[str, Any]
    ) -> dict[str, Any]:
        """把一次 GateReport 沉淀为 release_reports 行，返回完整报告 dict（含 id）。"""
        total = int(report.get("total") or 0)
        passed = int(report.get("passed") or 0)
        failed = int(report.get("failed") or 0)
        cases = [ReportCaseRow(**row) for row in report.get("cases", [])]
        created_at = datetime.now(timezone.utc).isoformat()
        with self._engine.begin() as db:
            seq = int(db.execute(text("SELECT nextval('storage_id_seq')")).scalar_one())
            rid = f"rr-{seq}"
            db.execute(
                text(
                    "INSERT INTO release_reports (id, tenant_id, seq, graph_id, target, "
                    "trigger, total, passed, failed, skipped, blocked, pass_rate, cases, "
                    "created_at) VALUES (:id, :tenant_id, :seq, :graph_id, :target, "
                    ":trigger, :total, :passed, :failed, :skipped, :blocked, :pass_rate, "
                    ":cases, :created_at)"
                ),
                {
                    "id": rid,
                    "tenant_id": self._tenant_id,
                    "seq": seq,
                    "graph_id": graph_id,
                    "target": report.get("target", "draft"),
                    "trigger": trigger,
                    "total": total,
                    "passed": passed,
                    "failed": failed,
                    "skipped": bool(report.get("skipped", total == 0)),
                    "blocked": bool(report.get("blocked", False)),
                    "pass_rate": round(passed / total, 4) if total > 0 else None,
                    "cases": json.dumps(
                        [row.model_dump() for row in cases], ensure_ascii=False
                    ),
                    "created_at": created_at,
                },
            )
        return ReleaseReport(
            id=rid,
            graph_id=graph_id,
            target=report.get("target", "draft"),
            trigger=trigger,
            total=total,
            passed=passed,
            failed=failed,
            skipped=bool(report.get("skipped", total == 0)),
            blocked=bool(report.get("blocked", False)),
            pass_rate=round(passed / total, 4) if total > 0 else None,
            cases=cases,
            created_at=created_at,
        ).model_dump()

    def list_summary(self, graph_id: str) -> list[dict[str, Any]]:
        """本图报告倒序摘要（不含 cases 键，形状同内存档）。"""
        with self._engine.connect() as db:
            rows = db.execute(
                text(
                    f"SELECT {_SUMMARY_COLS} FROM release_reports "
                    "WHERE tenant_id = :t AND graph_id = :g ORDER BY seq DESC"
                ),
                {"t": self._tenant_id, "g": graph_id},
            ).all()
        result = []
        for row in rows:
            payload = self._row_to_report(row, with_cases=False).model_dump()
            payload.pop("cases", None)
            result.append(payload)
        return result

    def list_all_summary(self, limit: int = 100) -> list[dict[str, Any]]:
        """跨图报告倒序摘要（不含 cases 键）；上限 clamp 1-200。"""
        bounded = max(1, min(int(limit), 200))
        with self._engine.connect() as db:
            rows = db.execute(
                text(
                    f"SELECT {_SUMMARY_COLS} FROM release_reports "
                    "WHERE tenant_id = :t ORDER BY seq DESC LIMIT :limit"
                ),
                {"t": self._tenant_id, "limit": bounded},
            ).all()
        result = []
        for row in rows:
            payload = self._row_to_report(row, with_cases=False).model_dump()
            payload.pop("cases", None)
            result.append(payload)
        return result

    def get(self, graph_id: str, report_id: str) -> dict[str, Any] | None:
        """按图取报告详情（含 cases）；不属于该图/租户或不存在返 None。"""
        with self._engine.connect() as db:
            row = db.execute(
                text(
                    f"SELECT {_DETAIL_COLS} FROM release_reports "
                    "WHERE tenant_id = :t AND graph_id = :g AND id = :id"
                ),
                {"t": self._tenant_id, "g": graph_id, "id": report_id},
            ).first()
        return self._row_to_report(row, with_cases=True).model_dump() if row else None

    def reset(self) -> None:
        """/api/demo/reset 删本租户报告（录制用例不清）。"""
        with self._engine.begin() as db:
            db.execute(
                text("DELETE FROM release_reports WHERE tenant_id = :t"),
                {"t": self._tenant_id},
            )
