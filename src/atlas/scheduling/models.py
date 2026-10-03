"""定时触发的记录视图（docs/68 §2.1／§2.2，打包 N）。

两档 store（进程内／PG）共用这里的模型与 `schedule_projection`，"两档投影逐键一致"
由单点函数保证，而不是靠两边各写一遍再对齐（docs/61 H2/H4 的同一条纪律）。

时刻一律 UTC：内存档直接持有字符串，PG 档读回的是 `TIMESTAMPTZ`，两者都经 `to_utc_iso`
归一后再进投影——同一个槽位在两档里必须落成同一个字符串键，否则"逐键一致"从投影开始就漏。
"""

import logging

logger = logging.getLogger(__name__)
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel

from atlas.scheduling.cron import CronExpressionError, next_fire_utc, parse_cron

#: 调度动作（docs/88 §3 P-4，打包 ZH；迁移 035）。`run`＝跑一次已发布钉版；
#: `reflect`＝跑一次反思 pass（只出建议，不改图、不发布）。
ScheduleAction = Literal["run", "reflect"]
DEFAULT_SCHEDULE_ACTION: ScheduleAction = "run"

#: 调度级并发策略（docs/08 打包 ZN，D41 ④，迁移 037）。`skip`＝同一调度上一次触发
#: 仍 busy（running/suspended）时跳过本槽（v1 逐字行为）；`allow`＝busy 也照常认领派发、
#: 并发起一条新 run。`queue`（排队等上一次完成再跑）明确缓做。
OverlapPolicy = Literal["skip", "allow"]
DEFAULT_OVERLAP_POLICY: OverlapPolicy = "skip"

#: 保留 schedule_id 命名空间（双下划线前缀，不与图内节点 id 混用）：反思是整图 pass，
#: 一图只保留一条 reflect 注册；`__primary__` 仅用于迁移 037 回填 v1 存量 run 行。
REFLECT_SCHEDULE_ID = "__reflect__"
LEGACY_PRIMARY_SCHEDULE_ID = "__primary__"


def tz_of(name: str) -> ZoneInfo | None:
    """IANA 名 → ZoneInfo；`None`（缺省/UTC/查不到）＝UTC 语义，逐字等价旧行为。

    保存期已被 DSL 校验（NODE_TRIGGER_TZ_INVALID），读路径再兜一层：查不到就按 UTC 处理，
    绝不在读/派发路径抛异常（与 `next_fire_at` 的"读路径不抛"同纪律）。
    """
    if not name or name == "UTC":
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning("tz_of: 未知时区名 %r 回退 UTC", name)
        return None


def to_utc_iso(value: datetime | str) -> str:
    """归一为 UTC ISO-8601（带偏移）；naive 按 UTC 解释，字符串原样规范化。"""
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            logger.warning("to_utc_iso: 无法解析时间字符串 %r，原样返回", value)
            return value
        value = parsed
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


class ScheduleRecord(BaseModel):
    """一张图的一条定时注册（PK `(tenant_id, graph_id, schedule_id)`）：发布时派生，只跑钉版。

    打包 ZN（D41 ③④）：`schedule_id` 唯一标识一条调度——run 调度 id 等于图内该定时
    触发节点 id（一图可挂多个定时节点 ⇒ 多条 run），reflect 为图级保留 `__reflect__`；
    `overlap_policy` 决定同一调度上一次触发仍 busy 时本槽跳过（skip，v1 行为）还是
    并发再起一条（allow）。

    打包 ZL（docs/08 打包 ZL 立项块）：`timezone` 只解释 cron 字段的墙上时间
    （IANA 名，迁移 030 加列，默认 "UTC"）；`catch_up_minutes` 是防抖窗口（1 分钟）之上
    额外放宽的追赶分钟数，默认 0＝严格不追赶（docs/68 D-5「宁漏不重跑」）。
    """

    tenant_id: str
    graph_id: str
    schedule_id: str
    action: ScheduleAction = DEFAULT_SCHEDULE_ACTION
    version: int
    cron: str
    timezone: str = "UTC"
    catch_up_minutes: int = 0
    overlap_policy: OverlapPolicy = DEFAULT_OVERLAP_POLICY
    enabled: bool = True
    created_at: str
    last_fired_at: str | None = None
    last_skipped_at: str | None = None
    skip_count: int = 0


class ScheduleFire(BaseModel):
    """一次实际派发的槽位认领（PK `(tenant_id, graph_id, schedule_id, slot_utc)`）。

    **只记实际派发**：重叠跳过不写本表（写了就等于白吃掉一个槽位，docs/68 §1 D-6）。
    """

    tenant_id: str
    graph_id: str
    schedule_id: str
    action: ScheduleAction = DEFAULT_SCHEDULE_ACTION
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
        logger.warning("next_fire_at: cron 表达式解析失败（graph=%s schedule=%s）返回 None", record.graph_id, record.schedule_id)
        return None
    upcoming = next_fire_utc(spec, now, tz=tz_of(record.timezone))
    return upcoming.isoformat() if upcoming is not None else None


def schedule_projection(record: ScheduleRecord, now: datetime) -> dict[str, Any]:
    """REST 投影（camelCase，与仓库其余投影同形）；两档共用，逐键一致。"""
    return {
        "graphId": record.graph_id,
        "scheduleId": record.schedule_id,
        "action": record.action,
        "version": record.version,
        "cron": record.cron,
        "timeZone": record.timezone,
        "catchUpMinutes": record.catch_up_minutes,
        "overlapPolicy": record.overlap_policy,
        "enabled": record.enabled,
        "createdAt": record.created_at,
        "lastFiredAt": record.last_fired_at,
        "lastSkippedAt": record.last_skipped_at,
        "skipCount": record.skip_count,
        "nextFireAt": next_fire_at(record, now),
    }
