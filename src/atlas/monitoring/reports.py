"""监控运行报表聚合纯函数（docs/113，打包 AG）。零新依赖。

口径：
- 按天分组：key 为 UTC 日 ``YYYY-MM-DD``（started_at 前 10 字符）。
- 按版本分组：key 为 ``v<int>``（resolved_version）；None（手动/草稿）为 ``manual-draft``。
- 分位数复用 metrics.percentile 的 nearest-rank 口径；空列表 → 空列表（不造零桶）。
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any, Literal

from .metrics import percentile
from .records import RunRecord

GroupBy = Literal["day", "version"]


def bucket_key(record: RunRecord, group_by: str) -> str:
    if group_by == "version":
        return f"v{record.resolved_version}" if record.resolved_version is not None else "manual-draft"
    return record.started_at[:10]


def aggregate_runs(runs: list[RunRecord], group_by: GroupBy = "day") -> list[dict[str, Any]]:
    """按天/版本聚合；桶按记录首次出现（时间序）排列。空列表 → 空列表。"""
    buckets: OrderedDict[str, list[RunRecord]] = OrderedDict()
    for record in sorted(runs, key=lambda r: r.started_at):
        key = bucket_key(record, group_by)
        buckets.setdefault(key, []).append(record)
    return [summarize(key, group) for key, group in buckets.items()]


def summarize(key: str, group: list[RunRecord]) -> dict[str, Any]:
    total = len(group)
    completed = sum(1 for r in group if r.status == "completed")
    errored = sum(1 for r in group if r.status == "error")
    cancelled = sum(1 for r in group if r.status == "cancelled")
    durations = [r.duration_ms for r in group]
    p50 = percentile(durations, 50)
    p95 = percentile(durations, 95)
    return {
        "key": key,
        "total": total,
        "completed": completed,
        "error": errored,
        "cancelled": cancelled,
        "success_rate": round(completed / total, 4) if total else 0,
        "duration_avg_ms": round(sum(durations) / total, 2) if total else 0,
        "duration_p50_ms": round(p50, 2) if p50 is not None else 0,
        "duration_p95_ms": round(p95, 2) if p95 is not None else 0,
    }
