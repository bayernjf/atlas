"""HTTP 出向重试与按 host 熔断测试（docs/32 §5，13 文档 U237/U238）。

时钟/睡眠/随机源全部注入，离线确定性、不真睡、零真实网络。
"""

from __future__ import annotations

import email.utils

import httpx
import pytest

from atlas.httpapi.resilience import (
    CircuitBreaker,
    CircuitOpenError,
    RetryPolicy,
)
from atlas.httpapi.service import HttpApiCallError, HttpApiClient


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def advance(self, delta: float) -> None:
        self.t += delta


def make_policy(**overrides):
    sleeps: list[float] = []
    policy = RetryPolicy(
        sleep=sleeps.append,
        now=lambda: 0.0,
        rng=lambda lo, hi: hi,  # 全抖动取上界，确定性
        **overrides,
    )
    return policy, sleeps


def resp(status: int = 200, headers=None) -> httpx.Response:
    return httpx.Response(status, headers=headers or {}, json={"ok": True})


# --- U237 RetryPolicy ---

def test_get_transport_error_retried_up_to_max_attempts_then_raised():
    policy, sleeps = make_policy(max_attempts=3)
    calls = {"n": 0}

    def send():
        calls["n"] += 1
        raise httpx.ConnectError("connection refused")

    with pytest.raises(httpx.ConnectError):
        policy.execute(send, method="GET")
    assert calls["n"] == 3
    assert len(sleeps) == 2  # 第 1、2 次失败后退避


def test_get_recovers_after_two_503():
    policy, sleeps = make_policy(max_attempts=3)
    seq = [resp(503), resp(503), resp(200)]
    calls = {"n": 0}

    def send():
        r = seq[calls["n"]]
        calls["n"] += 1
        return r

    out = policy.execute(send, method="GET")
    assert out.status_code == 200
    assert calls["n"] == 3
    assert len(sleeps) == 2


def test_transient_status_exhausted_returns_last_response_not_raises():
    policy, _ = make_policy(max_attempts=3)
    out = policy.execute(lambda: resp(503), method="GET")
    assert out.status_code == 503  # 拿到响应即返回（重试透明）


@pytest.mark.parametrize("method", ["POST", "PATCH"])
def test_non_idempotent_post_patch_not_retried_on_transport_error(method):
    policy, sleeps = make_policy(max_attempts=3)
    calls = {"n": 0}

    def send():
        calls["n"] += 1
        raise httpx.ReadTimeout("slow")

    with pytest.raises(httpx.ReadTimeout):
        policy.execute(send, method=method, idempotent=False)
    assert calls["n"] == 1
    assert sleeps == []


def test_post_retried_when_explicitly_idempotent():
    policy, _ = make_policy(max_attempts=3)
    seq = [httpx.ConnectError("x"), resp(200)]
    calls = {"n": 0}

    def send():
        item = seq[calls["n"]]
        calls["n"] += 1
        if isinstance(item, Exception):
            raise item
        return item

    out = policy.execute(send, method="POST", idempotent=True)
    assert out.status_code == 200
    assert calls["n"] == 2


@pytest.mark.parametrize("method", ["GET", "HEAD", "PUT", "DELETE"])
def test_naturally_idempotent_methods_retry(method):
    policy, _ = make_policy(max_attempts=2)
    calls = {"n": 0}

    def send():
        calls["n"] += 1
        raise httpx.ConnectError("x")

    with pytest.raises(httpx.ConnectError):
        policy.execute(send, method=method)
    assert calls["n"] == 2


@pytest.mark.parametrize("status", [400, 401, 403, 404, 500])
def test_non_transient_status_not_retried(status):
    policy, sleeps = make_policy(max_attempts=3)
    calls = {"n": 0}

    def send():
        calls["n"] += 1
        return resp(status)

    out = policy.execute(send, method="GET")
    assert out.status_code == status
    assert calls["n"] == 1
    assert sleeps == []


def test_retry_after_seconds_preferred_over_backoff():
    policy, sleeps = make_policy(max_attempts=3, max_delay=8)
    seq = [resp(429, headers={"Retry-After": "1"}), resp(200)]
    calls = {"n": 0}

    def send():
        r = seq[calls["n"]]
        calls["n"] += 1
        return r

    policy.execute(send, method="GET")
    assert sleeps == [1.0]


def test_retry_after_http_date_honored():
    clock = FakeClock()
    sleeps: list[float] = []
    policy = RetryPolicy(sleep=sleeps.append, now=clock.now, rng=lambda lo, hi: hi)
    future = email.utils.formatdate(clock.now() + 5, usegmt=True)
    seq = [resp(503, headers={"Retry-After": future}), resp(200)]
    calls = {"n": 0}

    def send():
        r = seq[calls["n"]]
        calls["n"] += 1
        return r

    policy.execute(send, method="GET")
    assert sleeps == [pytest.approx(5.0, abs=0.1)]


def test_backoff_full_jitter_doubles_ceiling():
    # rng 取上界：attempt0 上界 base_delay=0.5，attempt1 上界 1.0
    policy, _ = make_policy(base_delay=0.5, multiplier=2, max_delay=8)
    assert policy.backoff_delay(0) == 0.5
    assert policy.backoff_delay(1) == 1.0
    assert policy.backoff_delay(3) == 4.0


def test_backoff_capped_at_max_delay():
    policy, _ = make_policy(base_delay=1.0, multiplier=2, max_delay=8)
    assert policy.backoff_delay(10) == 8.0


# --- U238 CircuitBreaker ---

def test_opens_after_consecutive_failures():
    clock = FakeClock()
    breaker = CircuitBreaker(fail_threshold=3, cooldown=30, now=clock.now)
    host = "api.example.com"
    for _ in range(2):
        breaker.record_failure(host)
        breaker.before_call(host)  # 仍 closed，放行
    breaker.record_failure(host)  # 第 3 次 → open
    with pytest.raises(CircuitOpenError):
        breaker.before_call(host)
    assert breaker.state_of(host) == "open"


def test_success_resets_failure_count():
    clock = FakeClock()
    breaker = CircuitBreaker(fail_threshold=3, cooldown=30, now=clock.now)
    host = "h"
    breaker.record_failure(h := host)
    breaker.record_failure(host)
    breaker.record_success(host)
    breaker.record_failure(host)
    breaker.record_failure(host)
    breaker.before_call(host)  # 计数被成功清零，未达阈值
    assert breaker.state_of(host) == "closed"


def test_half_open_allows_one_probe_then_closes_on_success():
    clock = FakeClock()
    breaker = CircuitBreaker(fail_threshold=1, cooldown=30, now=clock.now)
    host = "h"
    breaker.record_failure(host)
    with pytest.raises(CircuitOpenError):
        breaker.before_call(host)
    clock.advance(30)
    breaker.before_call(host)  # 冷却到 → half-open，放行试探
    assert breaker.state_of(host) == "half-open"
    # 半开期间第二个请求不允许（仅一个试探名额）
    with pytest.raises(CircuitOpenError):
        breaker.before_call(host)
    breaker.record_success(host)
    assert breaker.state_of(host) == "closed"


def test_half_open_failure_reopens_and_resets_cooldown():
    clock = FakeClock()
    breaker = CircuitBreaker(fail_threshold=1, cooldown=30, now=clock.now)
    host = "h"
    breaker.record_failure(host)
    clock.advance(30)
    breaker.before_call(host)  # half-open
    breaker.record_failure(host)  # 试探失败 → 重新开闸
    assert breaker.state_of(host) == "open"
    with pytest.raises(CircuitOpenError):
        breaker.before_call(host)  # 冷却被重置，仍拦
    clock.advance(29)
    with pytest.raises(CircuitOpenError):
        breaker.before_call(host)
    clock.advance(1)
    breaker.before_call(host)  # 新冷却到，再进 half-open
    assert breaker.state_of(host) == "half-open"


def test_hosts_are_isolated():
    clock = FakeClock()
    breaker = CircuitBreaker(fail_threshold=1, cooldown=30, now=clock.now)
    breaker.record_failure("a.example.com")
    with pytest.raises(CircuitOpenError):
        breaker.before_call("a.example.com")
    breaker.before_call("b.example.com")  # 另一 host 不受影响
    assert breaker.state_of("b.example.com") == "closed"


# --- client 层集成：熔断快速失败、SSRF 不计数 ---

def _public_resolver(_host):
    return ["93.184.216.34"]


def _fast_client(handler, **breaker_kwargs):
    transport = httpx.MockTransport(handler)
    return HttpApiClient(
        base_url="http://api.example.com",
        client=httpx.Client(transport=transport),
        resolver=_public_resolver,
        retry=RetryPolicy(max_attempts=2, sleep=lambda _: None, rng=lambda lo, hi: 0),
        breaker=CircuitBreaker(now=(clk := FakeClock()).now, **breaker_kwargs),
    ), clk


def test_client_opens_circuit_and_fast_fails_without_calling_transport():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(503)

    client, _ = _fast_client(handler, fail_threshold=2, cooldown=30)
    for _ in range(2):  # 两次逻辑请求最终 503 → 开闸
        out = client.request("GET", "/x")
        assert out["status"] == 503
    before = calls["n"]
    with pytest.raises(HttpApiCallError) as exc_info:
        client.request("GET", "/x")
    assert exc_info.value.code == "HTTP_CIRCUIT_OPEN"
    assert calls["n"] == before  # 开闸后零外呼


def test_client_success_keeps_circuit_closed_on_4xx():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(404)

    client, _ = _fast_client(handler, fail_threshold=2, cooldown=30)
    for _ in range(5):
        out = client.request("GET", "/x")
        assert out["status"] == 404
    assert calls["n"] == 5  # 4xx 不计熔断，持续放行


def test_egress_denial_does_not_trip_circuit():
    # SSRF 拒绝发生在熔断之外：反复打内网目标不应开闸，且无外呼
    transport = httpx.MockTransport(lambda r: httpx.Response(200))
    breaker = CircuitBreaker(fail_threshold=1, cooldown=30)
    client = HttpApiClient(
        base_url="http://api.example.com",
        client=httpx.Client(transport=transport),
        resolver=_public_resolver,
        retry=RetryPolicy(max_attempts=1, sleep=lambda _: None),
        breaker=breaker,
    )
    for _ in range(3):
        with pytest.raises(HttpApiCallError) as exc_info:
            client.request("GET", "http://169.254.169.254/latest/meta-data")
        assert exc_info.value.code == "EGRESS_DENIED"
    # 公网目标仍可调用（熔断未被 SSRF 拒绝触发）
    out = client.request("GET", "/health")
    assert out["status"] == 200
