"""批量回放报告：模型与进程内沉淀存储（D26 报告 v1，2026-09-18 立项；03 `release_report`、04 §5.11 末）。

M9 发布门禁（``gate.run_release_gate``）每次对 latest 草稿即时产 GateReport，
响应完即丢。本模块补回归资产的**沉淀 + 按图历史 + 通过率趋势**：

- ``ReleaseReport`` 是 GateReport 的沉淀形态（纯超集：加 id/trigger/pass_rate/created_at）；
- ``ReportStore`` 进程内、每租户一个实例（挂 TenantServices，照 RoutingStore 先例），
  单锁 + deque ring 100，**不进 storage Repository 抽象、不 PG 化**（PG 随 D26 持久化批次）；
- 报告是运行产物：``/api/demo/reset`` 清空（同 monitoring runs / routing 状态），
  录制用例沿用「reset 不清除」测试资产语义。

分层（06 §6.11）：``gate.run_release_gate`` 保持纯函数不碰存储；record 由 API 层
在 release-gate 端点（manual）与 publish gate（publish-gate）两处调用。
"""

from __future__ import annotations

import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

ReportTrigger = Literal["manual", "publish-gate"]

# 每租户保留最近报告数（ring 自然淘汰；监控 RunRecord ring 200，报告体量更小、频率更低）
REPORT_RING_SIZE = 100


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ReportCaseRow(BaseModel):
    """逐例回放结果行，与 GateReport.cases 行同形（03 release_gate/release_report）。"""

    case_id: str
    name: str
    matches: bool
    replay_status: str
    note: str | None = None


class ReleaseReport(BaseModel):
    """一次批量回放门禁的沉淀报告（GateReport 纯超集）。"""

    id: str
    graph_id: str
    target: Literal["draft"] = "draft"
    trigger: ReportTrigger
    total: int
    passed: int
    failed: int
    skipped: bool
    blocked: bool
    pass_rate: float | None = None  # passed/total；total=0（skipped）为 null
    cases: list[ReportCaseRow] = Field(default_factory=list)
    created_at: str


class ReportStore:
    """进程内报告 ring（每租户一个；单锁；reset 清空）。"""

    def __init__(self, maxlen: int = REPORT_RING_SIZE) -> None:
        self._items: deque[ReleaseReport] = deque(maxlen=maxlen)
        self._counter = 0
        self._lock = threading.Lock()

    def record(
        self, *, graph_id: str, trigger: ReportTrigger, report: dict[str, Any]
    ) -> dict[str, Any]:
        """把一次 GateReport 沉淀为 ReleaseReport，返回完整报告 dict（含 id，纯超集）。

        ``report`` 为 ``gate.run_release_gate`` 的返回（或同形 dict）；total=0 时
        pass_rate=None（skipped 也沉淀，留「当时未覆盖」痕迹）。
        """
        total = int(report.get("total") or 0)
        passed = int(report.get("passed") or 0)
        with self._lock:
            self._counter += 1
            rid = f"rr-{self._counter}"
            saved = ReleaseReport(
                id=rid,
                graph_id=graph_id,
                target=report.get("target", "draft"),
                trigger=trigger,
                total=total,
                passed=passed,
                failed=int(report.get("failed") or 0),
                skipped=bool(report.get("skipped", total == 0)),
                blocked=bool(report.get("blocked", False)),
                pass_rate=(round(passed / total, 4) if total > 0 else None),
                cases=[ReportCaseRow(**row) for row in report.get("cases", [])],
                created_at=_now_iso(),
            )
            self._items.append(saved)
        return saved.model_dump()

    def list_summary(self, graph_id: str) -> list[dict[str, Any]]:
        """本图报告倒序摘要列表（不含 cases 详情；03 release_report 列表端点）。"""
        with self._lock:
            items = [item for item in self._items if item.graph_id == graph_id]
        return [
            {key: value for key, value in item.model_dump().items() if key != "cases"}
            for item in reversed(items)
        ]

    def get(self, graph_id: str, report_id: str) -> dict[str, Any] | None:
        """按图取报告详情（含 cases）；不属于该图或不存在返 None（API 层 404，跨租户不泄漏）。"""
        with self._lock:
            item = next(
                (
                    item
                    for item in self._items
                    if item.id == report_id and item.graph_id == graph_id
                ),
                None,
            )
            return item.model_dump() if item is not None else None

    def reset(self) -> None:
        """/api/demo/reset 清空（录制用例不清）。"""
        with self._lock:
            self._items.clear()
            self._counter = 0


# --- D26 报告导出（CSV；JSON 直接用 model_dump，端点加 attachment 头）-------------

_TRIGGER_ZH = {"manual": "手动门禁", "publish-gate": "发布门禁"}


def _bool_zh(value: Any) -> str:
    return "是" if bool(value) else "否"


def report_to_csv(report: dict[str, Any]) -> str:
    """把报告详情渲染为 CSV 文本（UTF-8，无 BOM；端点按需加 BOM 供 Excel 识别中文）。

    上半段为报告元信息（键,值），空行分隔后为逐用例结果表。stdlib csv，零新依赖。
    pass_rate 为 0–1 小数，展示为百分比；total=0（未覆盖）显「未覆盖」。
    """
    import csv
    import io

    buffer = io.StringIO()
    writer = csv.writer(buffer)

    pass_rate = report.get("pass_rate")
    pass_rate_text = f"{pass_rate * 100:.2f}%" if isinstance(pass_rate, (int, float)) else "未覆盖"
    writer.writerow(["报告ID", report.get("id", "")])
    writer.writerow(["图ID", report.get("graph_id", "")])
    writer.writerow(["目标", report.get("target", "draft")])
    writer.writerow(["触发方式", _TRIGGER_ZH.get(report.get("trigger"), report.get("trigger", ""))])
    writer.writerow(["生成时间(UTC)", report.get("created_at", "")])
    writer.writerow(["用例总数", report.get("total", 0)])
    writer.writerow(["通过", report.get("passed", 0)])
    writer.writerow(["失败", report.get("failed", 0)])
    writer.writerow(["无用例跳过", _bool_zh(report.get("skipped"))])
    writer.writerow(["阻塞发布", _bool_zh(report.get("blocked"))])
    writer.writerow(["通过率", pass_rate_text])
    writer.writerow([])
    writer.writerow(["用例ID", "用例名", "是否匹配", "回放状态", "备注"])
    for row in report.get("cases", []):
        writer.writerow(
            [
                row.get("case_id", ""),
                row.get("name", ""),
                _bool_zh(row.get("matches")),
                row.get("replay_status", ""),
                row.get("note") or "",
            ]
        )
    return buffer.getvalue()
