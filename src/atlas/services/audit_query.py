"""Audit log query parameter helpers (moved verbatim from api/main.py).

Kept HTTP-shaped (HTTPException on bad input) to preserve the existing
422 response bodies; the functions are pure apart from that failure mode.
"""

from typing import Any

from fastapi import HTTPException

from atlas.observability.audit import parse_bound


def audit_bounds(since: str | None, until: str | None) -> tuple[str | None, str | None]:
    """校验 since/until（docs/61 §4.2）：非法 ISO 或 since>until → 422 中文聚合。

    项目没有全局 RequestValidationError 中文处理器，故在端点内手工校验
    （照静默 PUT 先例）。
    """
    from datetime import timezone

    def _clean(value: str | None) -> str | None:
        return value.strip() if value and value.strip() else None

    lo, hi = _clean(since), _clean(until)
    errors: list[str] = []
    parsed: dict[str, Any] = {}
    for name, label, raw in (("since", "since（起始时间）", lo), ("until", "until（截止时间）", hi)):
        if raw is None:
            continue
        try:
            parsed[name] = parse_bound(raw).astimezone(timezone.utc)
        except ValueError:
            errors.append(f"{label}不是合法的 ISO-8601 时间：{raw}")
    if not errors and lo and hi and parsed["since"] > parsed["until"]:
        errors.append(f"since 不能晚于 until（{lo} > {hi}）")
    if errors:
        raise HTTPException(status_code=422, detail=errors)
    return lo, hi


def audit_cursor(cursor: int | None) -> int | None:
    if cursor is None:
        return None
    if int(cursor) < 1:
        raise HTTPException(status_code=422, detail=[f"cursor 必须为正整数游标，收到：{cursor}"])
    return int(cursor)
