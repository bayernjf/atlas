"""打包 AF（docs/112）Part 2：命名时区 —— U1300–U1304。"""

import datetime as dt

import pytest

from atlas.graph.conditions import ConditionEvalError, evaluate_expression

UTC = dt.timezone.utc


def at_16():
    return "datetime(2026,6,15,16,0)"


def at_3():
    return "datetime(2026,6,15,3,0)"


# U1300：dateOfInZone 跨日界
def test_u1300_date_of_in_zone_day_boundaries():
    assert evaluate_expression(f'dateOfInZone({at_16()},"Asia/Shanghai")', {}) == dt.date(2026, 6, 16)
    assert evaluate_expression(f'dateOfInZone({at_16()},"America/New_York")', {}) == dt.date(2026, 6, 15)
    assert evaluate_expression(f'dateOfInZone({at_3()},"Asia/Shanghai")', {}) == dt.date(2026, 6, 15)
    assert evaluate_expression(f'dateOfInZone({at_3()},"America/New_York")', {}) == dt.date(2026, 6, 14)


# U1301：hourOfInZone
def test_u1301_hour_of_in_zone():
    assert evaluate_expression(f'hourOfInZone({at_16()},"Asia/Shanghai")', {}) == 0
    assert evaluate_expression(f'hourOfInZone({at_16()},"America/New_York")', {}) == 12
    assert evaluate_expression(f'hourOfInZone({at_3()},"Asia/Shanghai")', {}) == 11
    assert evaluate_expression(f'hourOfInZone({at_3()},"America/New_York")', {}) == 23


# U1302：todayInZone（注入时钟）
@pytest.mark.parametrize("hour", [16, 3])
def test_u1302_today_in_zone(hour):
    now = dt.datetime(2026, 6, 15, hour, 0, tzinfo=UTC)
    expr_dt = f"datetime(2026,6,15,{hour},0)"
    for zone in ("Asia/Shanghai", "America/New_York"):
        expected = evaluate_expression(f'dateOfInZone({expr_dt},"{zone}")', {})
        assert evaluate_expression(f'todayInZone("{zone}")', {}, now=now) == expected


# U1303：hourInZone（注入时钟）
@pytest.mark.parametrize("hour", [16, 3])
def test_u1303_hour_in_zone(hour):
    now = dt.datetime(2026, 6, 15, hour, 0, tzinfo=UTC)
    expr_dt = f"datetime(2026,6,15,{hour},0)"
    for zone in ("Asia/Shanghai", "America/New_York"):
        expected = evaluate_expression(f'hourOfInZone({expr_dt},"{zone}")', {})
        assert evaluate_expression(f'hourInZone("{zone}")', {}, now=now) == expected


# U1304：非法时区
@pytest.mark.parametrize(
    "expr",
    [
        f'dateOfInZone({at_16()},"Not/AZone")',
        f'hourOfInZone({at_16()},"Not/AZone")',
        'todayInZone("Not/AZone")',
        'hourInZone("Not/AZone")',
    ],
)
def test_u1304_unknown_timezone(expr):
    with pytest.raises(ConditionEvalError) as exc:
        evaluate_expression(expr, {}, now=dt.datetime(2026, 6, 15, 16, tzinfo=UTC))
    assert exc.value.code == "COND_INVALID_TIMEZONE"


@pytest.mark.parametrize(
    "expr",
    [
        'hourInZone("")',
        "todayInZone(123)",
        f'dateOfInZone({at_16()}, 5)',
    ],
)
def test_u1304_invalid_timezone_argument(expr):
    with pytest.raises(ConditionEvalError) as exc:
        evaluate_expression(expr, {}, now=dt.datetime(2026, 6, 15, 16, tzinfo=UTC))
    assert exc.value.code == "COND_INVALID_TIMEZONE"


def test_u1304_utc_zone_accepted():
    # UTC 是合法 IANA 名，结果与输入同时刻。
    assert evaluate_expression(f'hourOfInZone({at_16()},"UTC")', {}) == 16
    assert evaluate_expression(f'dateOfInZone({at_16()},"UTC")', {}) == dt.date(2026, 6, 15)
