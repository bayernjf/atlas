"""打包 ZL（docs/08 打包 ZL 立项块，2026-10-03）：命名时区（IANA）＋补跑开关。

U1057–U1061 各带反向对照；U1062（前端时区选择＋预演按 tz 显示）是浏览器冒烟，
本文件只承后端四例。核心不变量：cron 字段按目标 tz 的**墙上时间**解释（zoneinfo 处理
DST），槽位／认领／投影仍归一 UTC；`catch_up_minutes` 是防抖窗口（1 分钟）之上额外
放宽的追赶分钟数，0＝严格不追赶（docs/68 §1 D-5「宁漏不重跑」逐字不变）。
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from atlas.scheduling.cron import next_fire_utc, parse_cron
from atlas.scheduling.engine import (
    ACTION_CLAIM_LOST,
    ACTION_FIRED,
    TickLedger,
    tick,
)
from atlas.scheduling.models import ScheduleRecord


def utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0, second: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)


def record(
    cron: str = "*/5 * * * *",
    *,
    timezone: str = "UTC",
    catch_up_minutes: int = 0,
    graph_id: str = "g1",
    schedule_id: str = "sch-1",
) -> ScheduleRecord:
    return ScheduleRecord(
        tenant_id="t1", graph_id=graph_id, schedule_id=schedule_id, version=2, cron=cron,
        timezone=timezone, catch_up_minutes=catch_up_minutes,
        enabled=True, created_at="2026-09-26T00:00:00+00:00",
    )


class Recorder:
    """假的认领表＋派发出口：认领按 (tenant, graph, schedule_id, slot) 幂等（同两档 store）。"""

    def __init__(self) -> None:
        self.claims: list[tuple[str, str, str, str]] = []
        self.dispatched: list[tuple[str, str]] = []

    def claim(self, item: ScheduleRecord, slot: datetime) -> bool:
        key = (item.tenant_id, item.graph_id, item.schedule_id,
               slot.replace(second=0, microsecond=0).isoformat())
        if key in self.claims:
            return False
        self.claims.append(key)
        return True

    def dispatch(self, item: ScheduleRecord, slot: datetime) -> None:
        self.dispatched.append((item.graph_id, slot.replace(second=0, microsecond=0).isoformat()))


# --- U1057 tz 槽位匹配：Asia/Shanghai 09:00 = UTC 01:00，同槽同认领键 -------------------

def test_u1057_shanghai_nine_oclock_maps_to_utc_0100_same_slot_same_claim_key():
    after = utc(2026, 9, 25, 0, 0)
    shanghai = next_fire_utc(parse_cron("0 9 * * *"), after, tz=ZoneInfo("Asia/Shanghai"))
    utc_direct = next_fire_utc(parse_cron("0 1 * * *"), after)
    assert shanghai == utc_direct == utc(2026, 9, 25, 1, 0), "上海 09:00 的槽位必须是 UTC 01:00"

    # 引擎层反向对照：两条 record 在"当地 09:01"的 tick 认领同一个 UTC 槽位。
    local_morning = utc(2026, 9, 26, 1, 1, 10)  # 上海当地 09:01 = UTC 01:01
    sh_recorder = Recorder()
    tick(local_morning, [record("0 9 * * *", timezone="Asia/Shanghai")],
         sh_recorder.claim, sh_recorder.dispatch, ledger=TickLedger())
    utc_recorder = Recorder()
    tick(local_morning, [record("0 1 * * *", timezone="UTC")],
         utc_recorder.claim, utc_recorder.dispatch, ledger=TickLedger())
    assert sh_recorder.dispatched == utc_recorder.dispatched, "同槽（UTC 01:00）"
    assert sh_recorder.claims == utc_recorder.claims, "同认领键"


# --- U1058 DST 边界：America/New_York 切换周不跳槽/不重槽（zoneinfo 真 DST） ------------

def test_u1058_spring_forward_week_does_not_skip_a_slot():
    ny = ZoneInfo("America/New_York")
    spec = parse_cron("0 2 * * *")  # 当地每天 02:00
    slots: list[datetime] = []
    cursor = utc(2026, 3, 5, 0, 0)
    for _ in range(5):
        cursor = next_fire_utc(spec, cursor, tz=ny)
        slots.append(cursor)
    # 03-05/06/07 是 EST（02:00 = 07:00Z）；03-08 切换日 02:00 不存在 → 03:00 EDT
    # 仍 07:00Z——UTC 槽连续，不跳槽；03-09 起 EDT（02:00 = 06:00Z）。
    assert slots == [
        utc(2026, 3, 5, 7, 0),
        utc(2026, 3, 6, 7, 0),
        utc(2026, 3, 7, 7, 0),
        utc(2026, 3, 8, 7, 0),
        utc(2026, 3, 9, 6, 0),
    ]


def test_u1058_fall_back_week_does_not_repeat_a_slot():
    ny = ZoneInfo("America/New_York")
    spec = parse_cron("0 2 * * *")
    before = next_fire_utc(spec, utc(2026, 10, 31, 0, 0), tz=ny)   # 10-31 EDT 02:00 = 06:00Z
    switch_day = next_fire_utc(spec, before, tz=ny)                # 11-01：02:00 EDT（06:00Z）
    next_day = next_fire_utc(spec, switch_day, tz=ny)              # 11-02 EST 02:00 = 07:00Z
    assert before == utc(2026, 10, 31, 6, 0)
    # 回拨边界上绝对时刻 06:00Z（=02:00 EDT）zoneinfo 解析为 01:00 EST——墙钟 02:00 只
    # 触发一次（EST 段 07:00Z），从 10-31 到 11-02 无重复槽、无倒退，即"不重槽"。
    assert switch_day == utc(2026, 11, 1, 7, 0), "回拨日命中 EST 段 02:00，一天只触发一次"
    assert next_day == utc(2026, 11, 2, 7, 0)


# --- U1060 catch_up 补跑：停摆 3h、catch_up_minutes=180 只补最近一个未认领槽 ----------

def test_u1060_catch_up_replays_the_unclaimed_slot_once_and_never_a_claimed_one():
    item = record("*/5 * * * *", catch_up_minutes=180)
    # 进程最后看见 10:00 的槽，停摆到 13:02 醒来；13:00 的槽未认领且在放宽窗口内。
    boot = utc(2026, 9, 26, 13, 2, 10)
    recorder = Recorder()
    outcomes = tick(boot, [item], recorder.claim, recorder.dispatch, ledger=TickLedger())
    assert [o.action for o in outcomes] == [ACTION_FIRED]
    assert recorder.dispatched == [("g1", "2026-09-26T13:00:00+00:00")], (
        "只补最近一个未认领槽，12:55/12:50… 不补"
    )

    # 反向门：认领表保留（重启后 ledger 清了）、同一槽再来一个 tick——认领失败不派发。
    # 引擎对认领失败的回执是 claim_lost（docs/68 §2 三态之一），不是 fired——不重跑。
    reboot = TickLedger()
    again = tick(utc(2026, 9, 26, 13, 2, 40), [item],
                 recorder.claim, recorder.dispatch, ledger=reboot)
    assert [o.action for o in again] == [ACTION_CLAIM_LOST], "已认领槽不重跑"
    assert recorder.dispatched == [("g1", "2026-09-26T13:00:00+00:00")]


def test_u1060_zero_catch_up_still_never_catches_up_after_a_pause():
    """反向门：停摆 3h 但 catch_up_minutes 默认 0——行为与打包 N 逐字一致（U881 语义）。"""
    item = record("*/5 * * * *")
    recorder = Recorder()
    outcomes = tick(utc(2026, 9, 26, 13, 2, 10), [item],
                    recorder.claim, recorder.dispatch, ledger=TickLedger())
    assert [o.action for o in outcomes] == [], "默认 0 不追赶，13:00 槽已过窗口"
    assert recorder.dispatched == []


# --- U1061 默认零回归：显式 UTC/0 与缺省逐字等价（防超集漂移） -------------------------

def test_u1061_explicit_utc_and_zero_catch_up_are_byte_for_byte_equivalent_to_defaults():
    now = utc(2026, 9, 26, 10, 5, 10)  # 命中 10:05 槽（*/5）
    default_rec = record("*/5 * * * *")
    explicit_rec = record("*/5 * * * *", timezone="UTC", catch_up_minutes=0)
    a, b = Recorder(), Recorder()
    tick(now, [default_rec], a.claim, a.dispatch, ledger=TickLedger())
    tick(now, [explicit_rec], b.claim, b.dispatch, ledger=TickLedger())
    assert a.dispatched == b.dispatched == [("g1", "2026-09-26T10:05:00+00:00")]
    assert a.claims == b.claims
