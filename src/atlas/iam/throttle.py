"""登录失败节流（docs/31 §5，ADR T25）：进程内滑动窗口，600s 内 5 次失败即锁定。

多实例不共享计数（单实例边界）；多实例时 PG 化尝试记录，随部署批。
"""

from __future__ import annotations

import threading
from collections import defaultdict, deque
from dataclasses import dataclass, field

WINDOW_SECONDS = 600
MAX_FAILURES = 5


@dataclass
class LoginThrottle:
    window_seconds: int = WINDOW_SECONDS
    max_failures: int = MAX_FAILURES
    _failures: dict[str, deque[float]] = field(default_factory=lambda: defaultdict(deque))
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def _window(self, key: str, now: float) -> deque[float]:
        failures = self._failures[key]
        cutoff = now - self.window_seconds
        while failures and failures[0] <= cutoff:
            failures.popleft()
        return failures

    def is_locked(self, key: str, now: float) -> bool:
        with self._lock:
            return len(self._window(key, now)) >= self.max_failures

    def record_failure(self, key: str, now: float) -> None:
        with self._lock:
            self._window(key, now).append(now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)
