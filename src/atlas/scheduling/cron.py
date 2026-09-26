"""最小 5 字段 cron 子集求值（docs/68 §1 D-1／§1.1，ADR T30；零新依赖）。

支持：`*`、`*/n`、`a`、`a-b`、`a-b/n`、逗号列表、显式数值；`7` 归一为周日 `0`。
不支持：`?`、`L`、`W`、`#`、秒字段、`a/n`、月份名／星期名别名——一律中文报错拒绝，
不做"看不懂就当 `*`"的兜底（静默放宽调度频率比拒绝保存危险）。

时间口径只有 UTC（docs/68 §1 D-2）。本模块纯 stdlib、不读时钟、不触网：时刻一律由调用方
注入，所以"跨月／闰年／日与周取并集"这些语义可以在不起真时钟的情况下被测完。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

# 求值时域：`0 0 30 2 *` 这类"能解析但几乎永不出声"的表达式在保存期就拒（docs/68 §1.1 第 4 条）。
NEVER_FIRES_HORIZON_YEARS = 5
NEVER_FIRES_HORIZON_DAYS = NEVER_FIRES_HORIZON_YEARS * 366

# 日与周字段：字段文本恰为 `*` 才算"不受限"，两个都受限则取并集（标准 cron 规则）。
_STAR = "*"

_ALIASES_REJECTED = (
    "不支持该取值（本子集只认数字、`*`、`a-b`、`*/n`、`a-b/n` 与逗号列表，"
    "不支持 `?`/`L`/`W`/`#`、秒字段与月份名／星期名别名）"
)

_DAY_OF_WEEK_LABEL = "星期(0-7，0 与 7 均为周日)"


class CronExpressionError(ValueError):
    """cron 表达式不合本子集；消息为中文，直接进 422 的 detail。"""


@dataclass(frozen=True)
class CronSpec:
    """一条已解析的 cron（集合形式，匹配只做成员判定，不再回看原文）。"""

    expression: str
    minutes: frozenset[int]
    hours: frozenset[int]
    days_of_month: frozenset[int]
    months: frozenset[int]
    days_of_week: frozenset[int]
    dom_restricted: bool = True
    dow_restricted: bool = True


_FIELDS: tuple[tuple[str, int, int, bool], ...] = (
    ("分钟", 0, 59, False),
    ("小时", 0, 23, False),
    ("日期", 1, 31, False),
    ("月份", 1, 12, False),
    (_DAY_OF_WEEK_LABEL, 0, 7, True),
)


def _reject(expression: str, reason: str) -> CronExpressionError:
    return CronExpressionError(f"Cron 表达式 {expression!r} 无效：{reason}")


def _to_int(token: str, label: str, lo: int, hi: int) -> int:
    if not token.isdigit():
        raise ValueError(f"{label}字段的 {token!r} 非法：{_ALIASES_REJECTED}")
    value = int(token)
    if not lo <= value <= hi:
        raise ValueError(f"{label}字段取值 {value} 超出范围 {lo}-{hi}")
    return value


def _expand(start: int, end: int, step: int, label: str) -> set[int]:
    if start > end:
        raise ValueError(f"{label}字段区间起点 {start} 大于终点 {end}（不支持回绕区间）")
    return set(range(start, end + 1, step))


def _parse_term(term: str, label: str, lo: int, hi: int) -> set[int]:
    base, slash, step_text = term.partition("/")
    step = 1
    if slash:
        if not step_text.isdigit() or int(step_text) < 1:
            raise ValueError(f"{label}字段步长 {step_text!r} 必须是 ≥1 的整数")
        step = int(step_text)

    if base == _STAR:
        return _expand(lo, hi, step, label)
    if base == "":
        raise ValueError(f"{label}字段缺少取值")
    if "-" in base:
        head, _, tail = base.partition("-")
        start = _to_int(head, label, lo, hi)
        end = _to_int(tail, label, lo, hi)
        return _expand(start, end, step, label)
    if slash:
        raise ValueError(f"{label}字段的 {term!r} 不在支持的语法内（步进需写作 `*/n` 或 `a-b/n`）")
    return {_to_int(base, label, lo, hi)}


def _parse_field(
    text: str,
    label: str,
    lo: int,
    hi: int,
    expression: str,
    normalize_sunday_seven: bool = False,
) -> frozenset[int]:
    if not text:
        raise _reject(expression, f"{label}字段为空")
    values: set[int] = set()
    for term in text.split(","):
        try:
            values |= _parse_term(term, label, lo, hi)
        except ValueError as exc:
            raise _reject(expression, str(exc)) from exc
    if not values:
        raise _reject(expression, f"{label}字段没有任何取值")
    if normalize_sunday_seven and 7 in values:
        values = (values - {7}) | {0}
    return frozenset(values)


def _is_restricted(text: str) -> bool:
    return text.strip() != _STAR


def parse_cron(expression: str) -> CronSpec:
    """解析 5 字段 cron；不合子集一律抛 CronExpressionError（中文消息）。"""
    if not isinstance(expression, str):
        raise _reject(str(expression), "必须是字符串")
    text = expression.strip()
    if not text:
        raise _reject(expression, "为空")
    parts = text.split()
    if len(parts) != len(_FIELDS):
        raise _reject(
            expression,
            f"需要 {len(_FIELDS)} 个字段（分 时 日 月 周），当前 {len(parts)} 个"
            "（不支持秒字段与 @daily 等别名）",
        )
    fields = [
        _parse_field(part, label, lo, hi, expression, normalize_sunday_seven=sunday)
        for part, (label, lo, hi, sunday) in zip(parts, _FIELDS)
    ]
    return CronSpec(
        expression=text,
        minutes=fields[0],
        hours=fields[1],
        days_of_month=fields[2],
        months=fields[3],
        days_of_week=fields[4],
        dom_restricted=_is_restricted(parts[2]),
        dow_restricted=_is_restricted(parts[4]),
    )


def cron_dow(day: datetime) -> int:
    """Python weekday()（0=周一）→ cron 编号（0=周日）。"""
    return (day.weekday() + 1) % 7


def day_matches(spec: CronSpec, day: datetime) -> bool:
    """日／月／周的成员判定；日与周**都**受限时取并集，否则只看受限的那个。"""
    if day.month not in spec.months:
        return False
    by_dom = day.day in spec.days_of_month
    by_dow = cron_dow(day) in spec.days_of_week
    if spec.dom_restricted and spec.dow_restricted:
        return by_dom or by_dow
    if spec.dom_restricted:
        return by_dom
    if spec.dow_restricted:
        return by_dow
    return True


def matches(spec: CronSpec, moment: datetime) -> bool:
    """moment 是否正好落在某个触发分钟上（秒／微秒忽略）。"""
    return (
        day_matches(spec, moment)
        and moment.hour in spec.hours
        and moment.minute in spec.minutes
    )


def _start_of_next_day(moment: datetime) -> datetime:
    return datetime.combine(
        (moment + timedelta(days=1)).date(), datetime.min.time(), tzinfo=moment.tzinfo
    )


def _start_of_next_hour(moment: datetime) -> datetime:
    return datetime.combine(moment.date(), moment.time().replace(minute=0), tzinfo=moment.tzinfo) \
        + timedelta(hours=1)


def _start_of_next_month(moment: datetime) -> datetime:
    year, month = (moment.year + 1, 1) if moment.month == 12 else (moment.year, moment.month + 1)
    return datetime(year, month, 1, tzinfo=moment.tzinfo)


def next_fire_utc(
    spec: CronSpec,
    after: datetime,
    horizon_days: int = NEVER_FIRES_HORIZON_DAYS,
) -> datetime | None:
    """`after` 之后第一个触发分钟（严格大于，秒位归零）；horizon 内无解返回 None。

    按 月→日→时→分 逐级跳过，不是逐分钟扫描：`0 0 29 2 *` 这种一次调用要走 4 个日级跳跃，
    逐分钟扫 5 年是 260 万次循环。
    """
    cursor = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    limit = after + timedelta(days=horizon_days)
    while cursor <= limit:
        if cursor.month not in spec.months:
            cursor = _start_of_next_month(cursor)
            continue
        if not day_matches(spec, cursor):
            cursor = _start_of_next_day(cursor)
            continue
        if cursor.hour not in spec.hours:
            cursor = _start_of_next_hour(cursor)
            continue
        if cursor.minute not in spec.minutes:
            cursor += timedelta(minutes=1)
            continue
        return cursor
    return None


def validate_cron(expression: str, now: datetime | None = None) -> CronSpec:
    """保存期校验：语法合法 **且** 在时域内至少有一个触发分钟。

    返回解析结果供调用方复用。"能解析但永不触发"必须在这里拒掉——它落库后的表现是
    "图永远不会自己跑"，与 docs/63 §0A N4 要修的真空完全同形。
    """
    spec = parse_cron(expression)
    reference = now if now is not None else datetime.now(timezone.utc)
    if next_fire_utc(spec, reference) is None:
        raise _reject(expression, f"未来 {NEVER_FIRES_HORIZON_YEARS} 年内没有任何触发时刻")
    return spec


def previous_fire_utc(spec: CronSpec, moment: datetime, lookback_minutes: int = 1) -> datetime | None:
    """`moment` 所在分钟起往前最多 `lookback_minutes` 分钟里最近的触发分钟。

    窗口刻意只有一分钟宽：它就是"不补跑"的实现处（docs/68 §1 D-5）。放宽窗口＝开一条
    追赶逻辑，进程停三小时会把它没看见的槽位全补一遍。
    """
    cursor = moment.replace(second=0, microsecond=0)
    for _ in range(lookback_minutes + 1):
        if matches(spec, cursor):
            return cursor
        cursor -= timedelta(minutes=1)
    return None
