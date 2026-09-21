"""HTTP 出向重试与按 host 熔断（docs/32 §5，P0 批 3）。

纯进程内策略对象，标准库实现，时钟/睡眠/随机源均可注入（离线单测不真睡）。

- :class:`RetryPolicy`：仅对传输层异常（``httpx.TransportError``，含超时）与
  临时网关状态（429/502/503/504，429 优先尊重 ``Retry-After``）做指数退避 +
  全抖动重试；``GET/HEAD/PUT/DELETE`` 视为幂等可重试，``POST/PATCH`` 仅当
  显式声明 ``idempotent`` 才重试。拿到任何 HTTP 响应都返回（保持「拿到响应即
  成功、仅传输层失败失败」的既有语义，重试对最终结果透明）。
- :class:`CircuitBreaker`：按 host 进程内计数，连续失败达阈值开闸，开闸期间
  不发请求直接快速失败 :class:`CircuitOpenError`（HTTP_CIRCUIT_OPEN），冷却后
  半开放行一个试探，成功闭合、失败重新开闸。4xx（除 429）不计失败。

SSRF egress 拒绝在这两者之外（配置/校验错误，不重试、不计熔断）。不跨实例
共享状态（多实例随部署批）。
"""

from __future__ import annotations

import email.utils
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

import httpx

# 视为对端临时不可用、可重试且计熔断的状态
TRANSIENT_STATUSES = frozenset({429, 502, 503, 504})

# 视为天然幂等、出向失败可安全重试的方法
IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "PUT", "DELETE"})

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BASE_DELAY = 0.5
DEFAULT_MAX_DELAY = 8.0
DEFAULT_MULTIPLIER = 2.0
DEFAULT_FAIL_THRESHOLD = 5
DEFAULT_COOLDOWN = 30.0


class CircuitOpenError(Exception):
    """熔断器开启，请求未发出；code 固定 HTTP_CIRCUIT_OPEN。"""

    code = "HTTP_CIRCUIT_OPEN"

    def __init__(self, host: str) -> None:
        super().__init__(f"目标 {host} 熔断器开启，快速失败")
        self.host = host


@dataclass
class _HostState:
    failures: int = 0
    state: str = "closed"  # closed / open / half-open
    opened_at: float = 0.0
    half_open_trial_used: bool = False


class RetryPolicy:
    """指数退避 + 全抖动重试策略（不在线程间共享状态，无状态判定可复用）。"""

    def __init__(
        self,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        base_delay: float = DEFAULT_BASE_DELAY,
        max_delay: float = DEFAULT_MAX_DELAY,
        multiplier: float = DEFAULT_MULTIPLIER,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.monotonic,
        rng: Callable[[float, float], float] = random.uniform,
    ) -> None:
        self.max_attempts = max(1, max_attempts)
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.multiplier = multiplier
        self._sleep = sleep
        self._now = now
        self._rng = rng

    def method_allows_retry(self, method: str, idempotent: bool) -> bool:
        """GET/HEAD/PUT/DELETE 天然幂等；POST/PATCH 需显式声明幂等。"""
        verb = method.upper()
        if verb in IDEMPOTENT_METHODS:
            return True
        return idempotent

    @staticmethod
    def is_transient_exception(exc: BaseException) -> bool:
        return isinstance(exc, httpx.TransportError)

    @staticmethod
    def is_transient_status(status_code: int) -> bool:
        return status_code in TRANSIENT_STATUSES

    def backoff_delay(self, attempt: int) -> float:
        """第 attempt 次失败后的全抖动延迟（attempt 从 0 起）。"""
        ceiling = min(self.max_delay, self.base_delay * (self.multiplier ** attempt))
        if ceiling <= 0:
            return 0.0
        return self._rng(0.0, ceiling)

    def retry_after_delay(self, response: httpx.Response) -> float | None:
        """解析 Retry-After（秒或 HTTP 日期）；缺失/非法返回 None。

        为避免服务端给超大值导致单请求无界等待，统一封顶 max_delay。
        """
        raw = response.headers.get("Retry-After")
        if not raw:
            return None
        raw = raw.strip()
        delay: float | None = None
        if raw.isdigit():
            delay = float(raw)
        else:
            parsed = email.utils.parsedate_to_datetime(raw)
            if parsed is not None:
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                delay = parsed.timestamp() - self._now()
        if delay is None or delay < 0:
            return 0.0
        return min(delay, self.max_delay)

    def execute(
        self,
        send: Callable[[], httpx.Response],
        *,
        method: str,
        idempotent: bool = False,
    ) -> httpx.Response:
        """调用 ``send()`` 直至成功/不可重试/次数用尽。

        - 传输异常重试用尽时，抛出最后一次异常；
        - 临时状态重试用尽时，返回最后一个响应（交由调用方计熔断，但仍按
          「拿到响应即成功」返回）。
        """
        allows = self.method_allows_retry(method, idempotent)
        last_exc: httpx.TransportError | None = None
        for attempt in range(self.max_attempts):
            try:
                response = send()
            except httpx.TransportError as exc:
                last_exc = exc
                if not allows or attempt >= self.max_attempts - 1:
                    raise
                self._sleep(self.backoff_delay(attempt))
                continue

            if self.is_transient_status(response.status_code) and allows and attempt < self.max_attempts - 1:
                ra = self.retry_after_delay(response)
                self._sleep(ra if ra is not None else self.backoff_delay(attempt))
                continue
            return response

        if last_exc is not None:  # pragma: no cover - 循环内已兜底
            raise last_exc
        raise RuntimeError("retry loop exited without response or exception")  # pragma: no cover


class CircuitBreaker:
    """按 host 的进程内熔断器（closed/open/half-open）。"""

    def __init__(
        self,
        fail_threshold: int = DEFAULT_FAIL_THRESHOLD,
        cooldown: float = DEFAULT_COOLDOWN,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self.fail_threshold = max(1, fail_threshold)
        self.cooldown = cooldown
        self._now = now
        self._hosts: dict[str, _HostState] = {}

    def _state(self, host: str) -> _HostState:
        state = self._hosts.get(host)
        if state is None:
            state = _HostState()
            self._hosts[host] = state
        return state

    def state_of(self, host: str) -> str:
        """观测用：返回当前对外生效状态（冷却到期的 open 在下次调用前仍显示 open）。"""
        return self._state(host).state

    def before_call(self, host: str) -> None:
        """发请求前调用；开闸且未到冷却抛 :class:`CircuitOpenError`。"""
        state = self._state(host)
        if state.state != "open":
            if state.state == "half-open" and state.half_open_trial_used:
                raise CircuitOpenError(host)
            return
        if self._now() - state.opened_at >= self.cooldown:
            # 冷却到：进入半开，发放唯一试探名额（本次放行，其后调用拦至有结果）
            state.state = "half-open"
            state.half_open_trial_used = True
            return
        raise CircuitOpenError(host)

    def record_success(self, host: str) -> None:
        state = self._state(host)
        state.state = "closed"
        state.failures = 0
        state.half_open_trial_used = False

    def record_failure(self, host: str) -> None:
        """记录一次临时失败（传输异常或 429/502/503/504）。"""
        state = self._state(host)
        if state.state == "half-open":
            # 试探失败：重新开闸并重置冷却
            state.state = "open"
            state.opened_at = self._now()
            state.half_open_trial_used = False
            return
        state.failures += 1
        if state.failures >= self.fail_threshold:
            state.state = "open"
            state.opened_at = self._now()
