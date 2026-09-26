"""定时触发的记录视图（docs/68 §2.1／§2.2，打包 N）。

两档 store（进程内／PG）共用这里的模型与 `schedule_projection`，"两档投影逐键一致"
由单点函数保证，而不是靠两边各写一遍再对齐（docs/61 H2/H4 的同一条纪律）。

时刻一律 UTC：内存档直接持有字符串，PG 档读回的是 `TIMESTAMPTZ`，两者都经 `to_utc_iso`
归一后再进投影——同一个槽位在两档里必须落成同一个字符串键，否则"逐键一致"从投影开始就漏。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel

from atlas.scheduling.cron import CronExpressionError, next_fire_utc, parse_cron


def to_utc_iso(value: datetime | str) -> str:
    """归一为 UTC ISO-8601（带偏移）；naive 按 UTC 解释，字符串原样规范化。"""
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
        value = parsed
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


class ScheduleRecord(BaseModel):
    """一张图的一条定时注册（PK `(tenant_id, graph_id)`）：发布时派生，只跑钉版。"""

    tenant_id: str
    graph_id: str
    version: int
    cron: str
    enabled: bool = True
    created_at: str
    last_fired_at: str | None = None
    last_skipped_at: str | None = None
    skip_count: int = 0


class ScheduleFire(BaseModel):
    """一次实际派发的槽位认领（PK `(tenant_id, graph_id, slot_utc)`）。

    **只记实际派发**：重叠跳过不写本表（写了就等于白吃掉一个槽位，docs/68 §1 D-6）。
    """

    tenant_id: str
    graph_id: str
    slot_utc: str
    fired_at: str


def slot_key(slot: datetime) -> str:
    """槽位的认领键：分钟归零后的 UTC ISO。"""
    return to_utc_iso(slot.replace(second=0, microsecond=0))


def next_fire_at(record: ScheduleRecord, now: datetime) -> str | None:
    """下一次触发时刻（UTC ISO）；cron 读不出结果时返回 None，绝不在读路径抛异常。"""
    try:
        spec = parse_cron(record.cron)
    except CronExpressionError:
        return None
    upcoming = next_fire_utc(spec, now)
    return upcoming.isoformat() if upcoming is not None else None


def schedule_projection(record: ScheduleRecord, now: datetime) -> dict[str, Any]:
    """REST 投影（camelCase，与仓库其余投影同形）；两档共用，逐键一致。"""
    return {
        "graphId": record.graph_id,
        "version": record.version,
        "cron": record.cron,
        "enabled": record.enabled,
        "createdAt": record.created_at,
        "lastFiredAt": record.last_fired_at,
        "lastSkippedAt": record.last_skipped_at,
        "skipCount": record.skip_count,
        "nextFireAt": next_fire_at(record, now),
    }
