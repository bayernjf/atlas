# -*- coding: utf-8 -*-
"""最小 cron 子集的语义（docs/68 §4 U874–U879，打包 N）。

纯函数、注入时刻：这些用例不起真时钟、不等分钟翻转，所以"跨月/闰年/日与周取并集"
都是确定性的。反向可证性靠 U877 的互逆断言（`next_fire` 给的槽必须 `matches`，
且两槽之间不得还有匹配），不是靠写死一个期望时间。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from atlas.scheduling.cron import (
    CronExpressionError,
    day_matches,
    matches,
    next_fire_utc,
    parse_cron,
    previous_fire_utc,
    validate_cron,
)


def utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0, second: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)


# --- U874 语法展开 -------------------------------------------------------

def test_u874_star_step_and_range_step_expand():
    assert parse_cron("*/5 * * * *").minutes == frozenset(range(0, 60, 5))
    assert parse_cron("15 10-14/2 * * *").hours == frozenset({10, 12, 14})
    assert parse_cron("0 0 1,15 * *").days_of_month == frozenset({1, 15})
    spec = parse_cron("0 9 * * 1-5")
    assert spec.minutes == frozenset({0})
    assert spec.hours == frozenset({9})
    assert spec.days_of_week == frozenset({1, 2, 3, 4, 5})


def test_u874_star_fields_cover_whole_range():
    spec = parse_cron("* * * * *")
    assert spec.minutes == frozenset(range(60))
    assert spec.hours == frozenset(range(24))
    assert spec.days_of_month == frozenset(range(1, 32))
    assert spec.months == frozenset(range(1, 13))
    assert spec.days_of_week == frozenset(range(7))
    # 两个 `*` 都不受限：每一天都算
    assert not spec.dom_restricted and not spec.dow_restricted


# --- U875 拒绝面（每条都要中文消息，不做"看不懂就当 *"） ----------------

@pytest.mark.parametrize("expression", [
    "",
    "   ",
    "* * * *",              # 4 字段
    "0 0 * * * *",          # 6 字段（秒）
    "@daily",               # 别名
    "0 0 * * MON",          # 星期名
    "0 0 * JAN *",          # 月份名
    "? 0 * * *",            # ? 不支持
    "0 0 * * L",            # L 不支持
    "0 0 W * *",            # W 不支持
    "0 0 * * 1#2",          # # 不支持
    "5/2 * * * *",          # 裸 a/n 不认（步进只认 */n 与 a-b/n）
    "0 0 32 * *",           # 日期越界
    "0-60 0 * * *",         # 区间越界
    "*/0 * * * *",          # 步长 0
    "0 0 22-2 * *",         # 回绕区间
    "0 0 * * 8",            # 星期越界（7 合法、8 不合法）
])
def test_u875_unsupported_expressions_are_rejected_with_chinese_reason(expression):
    with pytest.raises(CronExpressionError) as exc:
        parse_cron(expression)
    message = str(exc.value)
    assert expression in message or "字段" in message
    assert any("\u4e00" <= char <= "\u9fff" for char in message), message


# --- U876 7 == 周日 ------------------------------------------------------

def test_u876_seven_is_sunday_and_is_normalised():
    seven = parse_cron("0 0 * * 7")
    zero = parse_cron("0 0 * * 0")
    assert seven.days_of_week == zero.days_of_week == frozenset({0})
    sunday = utc(2026, 9, 27)          # 2026-09-27 是周日
    assert sunday.weekday() == 6
    assert day_matches(seven, sunday) and day_matches(zero, sunday)
    assert not day_matches(seven, utc(2026, 9, 28))


# --- U877 next_fire 与 matches 互逆 -------------------------------------

@pytest.mark.parametrize("expression", [
    "*/7 * * * *", "0 * * * *", "15 10-14/2 * * *", "30 8 1,15 * *",
    "0 9 * * 1-5", "55 23 * * 6", "0 0 29 2 *",
])
def test_u877_next_fire_returns_a_matching_slot_with_nothing_matching_between(expression):
    spec = parse_cron(expression)
    after = utc(2026, 9, 26, 12, 34, 56)
    first = next_fire_utc(spec, after)
    assert first is not None
    assert first > after
    assert matches(spec, first)
    # 互逆断言：`after` 与 `first` 之间的每一分钟都不该匹配——否则 next_fire 漏了更早的槽
    cursor = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    while cursor < first:
        assert not matches(spec, cursor), f"{expression} 在 {cursor} 有被漏掉的更早槽位"
        cursor += timedelta(minutes=1)


def test_u877_crosses_hour_month_and_leap_year():
    assert next_fire_utc(parse_cron("0 * * * *"), utc(2026, 9, 26, 12, 30)) == utc(2026, 9, 26, 13)
    assert next_fire_utc(parse_cron("0 0 1 * *"), utc(2026, 9, 26)) == utc(2026, 10, 1)
    assert next_fire_utc(parse_cron("0 0 1 1 *"), utc(2026, 9, 26)) == utc(2027, 1, 1)
    # 2 月 29 只在闰年出得来：2026-09-26 之后的下一个是 2028-02-29
    assert next_fire_utc(parse_cron("0 0 29 2 *"), utc(2026, 9, 26)) == utc(2028, 2, 29)
    # 跨年：12 月之后必须回到次年 1 月，不是停在 12 月 31 日
    assert next_fire_utc(parse_cron("0 0 15 3 *"), utc(2026, 12, 31, 23, 59)) == utc(2027, 3, 15)


def test_u877_previous_fire_only_looks_back_the_window_it_was_given():
    spec = parse_cron("*/5 * * * *")
    assert previous_fire_utc(spec, utc(2026, 9, 26, 10, 5, 30)) == utc(2026, 9, 26, 10, 5)
    assert previous_fire_utc(spec, utc(2026, 9, 26, 10, 3)) is None
    assert previous_fire_utc(spec, utc(2026, 9, 26, 10, 3), lookback_minutes=3) == utc(2026, 9, 26, 10, 0)


# --- U878 永不触发的表达式在保存期就拒 ----------------------------------

@pytest.mark.parametrize("expression", ["0 0 30 2 *", "0 0 31 2 *", "0 0 31 4 *"])
def test_u878_expressions_that_never_fire_are_rejected_at_save_time(expression):
    now = utc(2026, 9, 26)
    assert next_fire_utc(parse_cron(expression), now) is None
    with pytest.raises(CronExpressionError) as exc:
        validate_cron(expression, now=now)
    assert "没有任何触发时刻" in str(exc.value)


def test_u878_validate_cron_returns_the_parsed_spec_and_rejects_garbage():
    spec = validate_cron("*/5 * * * *", now=utc(2026, 9, 26))
    assert spec.minutes == frozenset(range(0, 60, 5))
    with pytest.raises(CronExpressionError):
        validate_cron("nope", now=utc(2026, 9, 26))


# --- U879 日与周的并集规则（docs/68 §1.1 第 1 条）------------------------

def test_u879_both_restricted_days_take_the_union():
    spec = parse_cron("0 0 1 * 1")          # 每月 1 号 或 每周一
    assert spec.dom_restricted and spec.dow_restricted
    assert day_matches(spec, utc(2026, 9, 28))    # 周一
    assert day_matches(spec, utc(2026, 10, 1))    # 1 号（周四）
    assert not day_matches(spec, utc(2026, 9, 30))  # 既不是 1 号也不是周一


def test_u879_only_one_restricted_field_is_the_one_that_applies():
    dom_only = parse_cron("0 0 15 * *")
    assert day_matches(dom_only, utc(2026, 9, 15))
    assert not day_matches(dom_only, utc(2026, 9, 14))
    dow_only = parse_cron("0 0 * * 3")
    assert day_matches(dow_only, utc(2026, 9, 30))   # 2026-09-30 是周三
    assert not day_matches(dow_only, utc(2026, 9, 29))
    every_day = parse_cron("0 0 * * *")
    assert day_matches(every_day, utc(2026, 9, 29)) and day_matches(every_day, utc(2026, 10, 1))
