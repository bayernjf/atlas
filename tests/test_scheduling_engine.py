# -*- coding: utf-8 -*-
"""调度引擎的派发判定（docs/68 §4 U880–U885，打包 N）。

引擎零 IO、时刻与回调全注入，所以这里既不用真时钟也不用库。三条承重语义各自都有
一条**反向用例**（U880-RG / U883-RG / U881-RG）：把那条机制短路掉之后，正例必须转红——
否则"只派发一次""不消耗认领""不补跑"都只是永真断言。
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from atlas.scheduling.engine import (
    ACTION_CLAIM_LOST,
    ACTION_FIRED,
    ACTION_SKIPPED_OVERLAP,
    TickLedger,
    tick,
)
from atlas.scheduling.models import ScheduleRecord


def utc(minute: int, second: int = 10) -> datetime:
    return datetime(2026, 9, 26, 10, minute, second, tzinfo=timezone.utc)


def record(cron: str = "*/5 * * * *", *, enabled: bool = True, graph_id: str = "g1") -> ScheduleRecord:
    return ScheduleRecord(
        tenant_id="t1", graph_id=graph_id, version=2, cron=cron, enabled=enabled,
        created_at="2026-09-26T00:00:00+00:00",
    )


class Recorder:
    """假的认领表＋派发出口：认领按 (tenant, graph, slot) 幂等，语义与两档 store 一致。"""

    def __init__(self, *, claim_always_true: bool = False) -> None:
        self.claims: list[tuple[str, str, str]] = []
        self.dispatched: list[tuple[str, str]] = []
        self._claim_always_true = claim_always_true

    def claim(self, item: ScheduleRecord, slot: datetime) -> bool:
        key = (item.tenant_id, item.graph_id, slot.replace(second=0, microsecond=0).isoformat())
        if not self._claim_always_true and key in self.claims:
            return False
        self.claims.append(key)
        return True

    def dispatch(self, item: ScheduleRecord, slot: datetime) -> None:
        self.dispatched.append((item.graph_id, slot.isoformat()))


# --- U880 同一个槽位只派发一次 ------------------------------------------

def test_u880_one_slot_dispatches_once_across_repeated_ticks():
    recorder = Recorder()
    item = record("*/5 * * * *")
    ledger = TickLedger()

    first = tick(utc(5), [item], recorder.claim, recorder.dispatch, ledger=ledger)
    again = tick(utc(5, 45), [item], recorder.claim, recorder.dispatch, ledger=ledger)
    next_slot = tick(utc(10), [item], recorder.claim, recorder.dispatch, ledger=ledger)

    assert [o.action for o in first] == [ACTION_FIRED]
    assert again == [], "同一个槽位被第二个 tick 再决定了一次"
    assert [o.action for o in next_slot] == [ACTION_FIRED]
    assert len(recorder.dispatched) == 2


def test_u880_rg_claim_is_the_authority_not_the_engine():
    """反向门：把认领短路成恒真（＝两个进程各自以为抢到），"只派发一次"立刻不成立。

    这条用例的意义是证明上一条靠的是 claim——引擎自己的 ledger 只在本进程内去重，
    它不承担跨进程安全（docs/68 §2.1）。
    """
    permissive = Recorder(claim_always_true=False)
    item = record()
    assert [o.action for o in tick(utc(5), [item], permissive.claim, permissive.dispatch,
                                   ledger=TickLedger())] == [ACTION_FIRED]
    lost = tick(utc(5, 30), [item], permissive.claim, permissive.dispatch, ledger=TickLedger())
    assert [o.action for o in lost] == [ACTION_CLAIM_LOST]
    assert len(permissive.dispatched) == 1, "认领表没起作用时第二次也该派发——这里必须已挡住"

    broken = Recorder(claim_always_true=True)  # 植缺陷：认领恒真
    assert [o.action for o in tick(utc(5), [item], broken.claim, broken.dispatch,
                                   ledger=TickLedger())] == [ACTION_FIRED]
    again = tick(utc(5, 30), [item], broken.claim, broken.dispatch, ledger=TickLedger())
    assert len(broken.dispatched) == 2, "认领恒真却仍然只派发一次＝这条反向门没咬到东西"


# --- U881 错过不补跑 ------------------------------------------------------

def test_u881_a_long_pause_fires_only_the_current_slot():
    recorder = Recorder()
    item = record("* * * * *")
    ledger = TickLedger()
    # 进程停了 3 小时（10:00 之后一直没跑），醒来只看眼前这一分钟
    outcomes = tick(utc(0), [item], recorder.claim, recorder.dispatch, ledger=ledger)
    assert [o.action for o in outcomes] == [ACTION_FIRED]

    ledger_after_pause = TickLedger()
    after_three_hours = datetime(2026, 9, 26, 13, 0, 5, tzinfo=timezone.utc)
    outcomes = tick(after_three_hours, [item], recorder.claim, recorder.dispatch,
                    ledger=ledger_after_pause)
    assert [o.action for o in outcomes] == [ACTION_FIRED]
    assert recorder.dispatched == [("g1", "2026-09-26T10:00:00+00:00"),
                                   ("g1", "2026-09-26T13:00:00+00:00")], "补跑了中间 179 个槽"


def test_u881_rg_widening_the_lookback_window_turns_catch_up_on():
    """反向门：把回看窗口放宽（＝打开追赶），"不补跑"这条立刻失效——证明承重的是窗口。"""
    item = record("*/5 * * * *")

    strict = Recorder()
    tick(utc(0), [item], strict.claim, strict.dispatch, ledger=TickLedger())
    gap = datetime(2026, 9, 26, 10, 14, 30, tzinfo=timezone.utc)
    after_gap = tick(gap, [item], strict.claim, strict.dispatch, ledger=TickLedger())
    assert after_gap == [] and strict.dispatched == [("g1", "2026-09-26T10:00:00+00:00")], \
        "默认窗口下 10:05/10:10 被补跑了"

    catching = Recorder()
    tick(utc(0), [item], catching.claim, catching.dispatch, ledger=TickLedger())
    tick(gap, [item], catching.claim, catching.dispatch, ledger=TickLedger(),
         lookback_minutes=15)
    assert catching.dispatched == [("g1", "2026-09-26T10:00:00+00:00"),
                                   ("g1", "2026-09-26T10:10:00+00:00")], \
        "窗口放宽后仍不追赶＝这条反向门没咬到东西"


# --- U882 开关 ----------------------------------------------------------

def test_u882_disabled_schedule_neither_dispatches_nor_claims():
    recorder = Recorder()
    outcomes = tick(utc(5), [record(enabled=False)], recorder.claim, recorder.dispatch,
                    ledger=TickLedger())
    assert outcomes == []
    assert recorder.claims == [] and recorder.dispatched == []


# --- U883 重叠跳过且不消耗认领 ------------------------------------------

def test_u883_overlap_skips_without_consuming_the_slot_claim():
    recorder = Recorder()
    item = record("*/5 * * * *")
    busy = tick(utc(5), [item], recorder.claim, recorder.dispatch,
                busy=lambda _: True, ledger=TickLedger())
    assert [o.action for o in busy] == [ACTION_SKIPPED_OVERLAP]
    assert recorder.claims == [], "跳过时写了认领＝一次重叠白吃掉一个槽位"

    # 下一个干净槽照常派发
    clean = tick(utc(10), [item], recorder.claim, recorder.dispatch,
                 busy=lambda _: False, ledger=TickLedger())
    assert [o.action for o in clean] == [ACTION_FIRED]


def test_u883_rg_short_circuiting_the_busy_check_loses_the_skip():
    """反向门：把"同图还在跑"短路成永不忙，跳过用例立刻没有 skipped 条目可断。"""
    recorder = Recorder()
    item = record("*/5 * * * *")
    outcomes = tick(utc(5), [item], recorder.claim, recorder.dispatch,
                    busy=lambda _: False, ledger=TickLedger())
    assert [o.action for o in outcomes] == [ACTION_FIRED]
    assert recorder.claims != [], "busy 判假时仍不派发＝引擎根本没看这个回调"


# --- U884/U885 引擎不能被一条坏数据带走 ---------------------------------

def test_u884_dispatch_failure_does_not_kill_the_tick():
    recorder = Recorder()
    failing = record(graph_id="bad")
    survivor = record(graph_id="ok")

    def explode_on_bad(item: ScheduleRecord, slot: datetime) -> None:
        if item.graph_id == "bad":
            raise RuntimeError("派发炸了")
        recorder.dispatch(item, slot)

    outcomes = tick(utc(5), [failing, survivor], recorder.claim, explode_on_bad,
                    ledger=TickLedger())
    assert [(o.graph_id, o.action) for o in outcomes] == [("ok", ACTION_FIRED)], \
        "失败的那条既不能被记成 fired，也不能带走同批其它调度"
    assert recorder.dispatched == [("ok", "2026-09-26T10:05:00+00:00")]
    # at-most-once 的代价照实承认：崩在派发里的槽位已被认领，本进程不会重试它
    assert len(recorder.claims) == 2


def test_u885_unparseable_cron_in_the_store_is_logged_and_skipped(caplog):
    recorder = Recorder()
    outcomes = tick(utc(5), [record(cron="rubbish"), record("*/5 * * * *", graph_id="ok")],
                    recorder.claim, recorder.dispatch, ledger=TickLedger())
    assert [o.graph_id for o in outcomes] == ["ok"]
    assert "无法解析" in caplog.text
    assert recorder.claims and len(recorder.claims) == 1


def test_tick_creates_its_own_ledger_when_none_is_passed():
    recorder = Recorder()
    assert [o.action for o in tick(utc(5), [record()], recorder.claim, recorder.dispatch)] \
        == [ACTION_FIRED]
