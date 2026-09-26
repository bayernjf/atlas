"""调度引擎：一个 tick 该不该派发，纯逻辑零 IO（docs/68 §2.1，打包 N）。

线程、存储、时钟都不在这里：`now` 由调用方注入，认领与派发是传进来的回调。这样
"同槽只派发一次""错过不补跑""重叠跳过不消耗认领"三条语义能在不起真时钟、不连库的情况下
被测完（照 `versioning/publish.py` 与 `storage/recovery.py` 的分层）。

三条不变量（改任何一条前先改 docs/68 §1）：
1. 派发权始终在 `claim` 上——它是跨进程/跨重启的权威；ledger 只做本进程"同一槽位不再做
   第二次决定"的去重，不承担安全性。
2. 只看 `now` 往前一分钟内的槽位，永不回看更早的槽 ⇒ 重启/暂停**不补跑**（D-5）。
3. 重叠判定在认领**之前**：跳过时不写认领表，白吃槽位的事情不会发生（D-6）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Iterable, Literal, Optional

from atlas.scheduling.cron import CronExpressionError, CronSpec, parse_cron, previous_fire_utc
from atlas.scheduling.models import ScheduleRecord, slot_key

logger = logging.getLogger(__name__)

ACTION_FIRED = "fired"
ACTION_SKIPPED_OVERLAP = "skipped_overlap"
ACTION_CLAIM_LOST = "claim_lost"

TickAction = Literal["fired", "skipped_overlap", "claim_lost"]

# 槽位回看窗口：1 分钟＝"这个 tick 没赶上，下一个 tick 还允许补做同一个槽"，
# 再宽就变成追赶逻辑了（docs/68 §1 D-5）。
DEFAULT_LOOKBACK_MINUTES = 1

# ledger 里一条去重记录保留多久；只为防"跑半年后集合无限长"，不承担语义。
_LEDGER_RETENTION = timedelta(hours=1)

ClaimSchedule = Callable[[ScheduleRecord, datetime], bool]
DispatchSchedule = Callable[[ScheduleRecord, datetime], None]
ScheduleIsBusy = Callable[[ScheduleRecord], bool]


@dataclass(frozen=True)
class TickOutcome:
    """一个 tick 里真正发生过决定的一条调度。"""

    tenant_id: str
    graph_id: str
    slot_utc: datetime
    action: TickAction


class TickLedger:
    """本进程的去重与解析缓存（随调度线程活，不参与跨进程判定）。"""

    def __init__(self) -> None:
        self._seen: dict[tuple[str, str, str], datetime] = {}
        self._specs: dict[str, Optional[CronSpec]] = {}

    def spec(self, expression: str) -> Optional[CronSpec]:
        """解析缓存；库里存进坏表达式（直改 DB／旧版本残留）时返回 None 并只记日志。"""
        if expression not in self._specs:
            try:
                self._specs[expression] = parse_cron(expression)
            except CronExpressionError as exc:
                logger.warning("调度表达式无法解析，跳过：%s", exc)
                self._specs[expression] = None
        return self._specs[expression]

    def seen(self, record: ScheduleRecord, slot: datetime) -> bool:
        return _slot_key(record, slot) in self._seen

    def mark(self, record: ScheduleRecord, slot: datetime) -> None:
        self._seen[_slot_key(record, slot)] = slot

    def prune(self, now: datetime) -> None:
        cutoff = now - _LEDGER_RETENTION
        for key, slot in list(self._seen.items()):
            if slot < cutoff:
                del self._seen[key]


def _slot_key(record: ScheduleRecord, slot: datetime) -> tuple[str, str, str]:
    return (record.tenant_id, record.graph_id, slot_key(slot))


def tick(
    now: datetime,
    schedules: Iterable[ScheduleRecord],
    claim: ClaimSchedule,
    dispatch: DispatchSchedule,
    *,
    busy: ScheduleIsBusy | None = None,
    ledger: TickLedger | None = None,
    lookback_minutes: int = DEFAULT_LOOKBACK_MINUTES,
) -> list[TickOutcome]:
    """评估一轮调度，返回**有动作**的条目（无槽位／已决定过的槽位不产生条目）。"""
    state = ledger if ledger is not None else TickLedger()
    state.prune(now)
    outcomes: list[TickOutcome] = []

    for record in schedules:
        if not record.enabled:
            continue
        spec = state.spec(record.cron)
        if spec is None:
            continue
        slot = previous_fire_utc(spec, now, lookback_minutes=lookback_minutes)
        if slot is None:
            continue
        if state.seen(record, slot):
            continue
        state.mark(record, slot)

        if busy is not None and busy(record):
            outcomes.append(TickOutcome(record.tenant_id, record.graph_id, slot, ACTION_SKIPPED_OVERLAP))
            continue
        if not claim(record, slot):
            outcomes.append(TickOutcome(record.tenant_id, record.graph_id, slot, ACTION_CLAIM_LOST))
            continue
        try:
            dispatch(record, slot)
        except Exception:  # noqa: BLE001 — 单条调度失败不能带走整条循环（docs/68 §4 U885）
            logger.exception("调度派发失败：tenant=%s graph=%s slot=%s",
                             record.tenant_id, record.graph_id, slot)
            continue
        outcomes.append(TickOutcome(record.tenant_id, record.graph_id, slot, ACTION_FIRED))

    return outcomes
