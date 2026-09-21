# -*- coding: utf-8 -*-
"""U230 登录节流滑动窗口纯逻辑（docs/31 §5，ADR T25）。"""

from __future__ import annotations

from atlas.iam.throttle import LoginThrottle


def test_four_failures_still_allowed() -> None:
    throttle = LoginThrottle()
    for i in range(4):
        throttle.record_failure("k", float(i))
    assert not throttle.is_locked("k", 4.0)


def test_fifth_failure_locks() -> None:
    throttle = LoginThrottle()
    for i in range(5):
        throttle.record_failure("k", float(i))
    assert throttle.is_locked("k", 5.0)
    # 另一把独立的键不受影响
    assert not throttle.is_locked("other", 5.0)


def test_window_slides_out() -> None:
    throttle = LoginThrottle()
    for i in range(5):
        throttle.record_failure("k", float(i))
    # t=599：全部 5 次仍在窗内 → 锁定；t=600：0s 的失败恰好滑出窗，剩 4 次 → 放行
    assert throttle.is_locked("k", 599.0)
    assert not throttle.is_locked("k", 600.0)


def test_success_resets_window() -> None:
    throttle = LoginThrottle()
    for i in range(4):
        throttle.record_failure("k", float(i))
    throttle.reset("k")
    for i in range(4):
        throttle.record_failure("k", 100.0 + i)
    assert not throttle.is_locked("k", 104.0)
