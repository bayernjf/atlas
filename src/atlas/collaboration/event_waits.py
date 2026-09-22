"""进程内事件等待 broker（wait 节点 waitType=event，docs/47；04 §5.5）。

wait 执行器按渲染后的 event_key 登记 pending（token=``wait-``+uuid4），随后在
Starlette 线程池工作线程上阻塞；信号经登录态 REST 按 key 广播或按 token 直投，
broker 以 monotonic deadline 判定超时。等待以 0.2s 切片轮询 ``is_cancelled``，
支持协作式取消（RunCancelled，节点边界语义）。

每租户一个、挂 TenantServices（内存/PG 两档均为内存实例，同 cancellation_broker）；
进程内、重启即失，不写中断帧、不支持多实例——持久化中断恢复随 docs/14 D19。
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field

_POLL_SLICE_SECONDS = 0.2


class WaitTokenNotFound(KeyError):
    """直投信号引用了未知或已取走的等待 token。"""


class WaitAlreadySignaled(Exception):
    """条目已被信号命中（首决生效，等待 worker 尚未取走）。"""


@dataclass
class _Pending:
    token: str
    event_key: str
    node_id: str
    graph_id: str
    deadline: float
    timeout_seconds: int
    event: threading.Event = field(default_factory=threading.Event)
    payload: dict | None = None

    @property
    def signaled(self) -> bool:
        return self.event.is_set()


@dataclass
class EventWaitBroker:
    """event_key → pending 集合的进程内等待/信号；全部方法线程安全。"""

    _pending: dict[str, _Pending] = field(default_factory=dict)
    _by_key: dict[str, set[str]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def request(
        self, *, event_key: str, node_id: str, graph_id: str, timeout_seconds: int
    ) -> str:
        token = f"wait-{uuid.uuid4().hex}"
        pending = _Pending(
            token=token,
            event_key=event_key,
            node_id=node_id,
            graph_id=graph_id,
            deadline=time.monotonic() + timeout_seconds,
            timeout_seconds=timeout_seconds,
        )
        with self._lock:
            self._pending[token] = pending
            self._by_key.setdefault(event_key, set()).add(token)
        return token

    def wait(self, token: str, *, is_cancelled=None) -> dict | None:
        """阻塞至信号到达或超时；返回信号 payload（dict）=signaled，None=超时。

        ``is_cancelled`` 零参回调每 0.2s 轮询，返回 True 时条目移除并抛
        RunCancelled（延迟 import 避循环依赖）。
        """
        deadline: float | None = None
        while True:
            with self._lock:
                pending = self._pending.get(token)
                if pending is None:
                    return None
                if pending.signaled:
                    payload = dict(pending.payload or {})
                    self._remove_locked(pending)
                    return payload
                deadline = pending.deadline
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._remove_locked(pending)
                    return None
            if is_cancelled is not None and is_cancelled():
                with self._lock:
                    pending = self._pending.get(token)
                    if pending is not None:
                        self._remove_locked(pending)
                from atlas.collaboration.cancellations import RunCancelled

                raise RunCancelled(pending.node_id if pending else token)
            time.sleep(min(_POLL_SLICE_SECONDS, remaining))

    def signal_key(self, event_key: str, payload: dict) -> int:
        """广播：释放全部同 key pending，返回释放条目数（无 pending 返 0）。"""
        with self._lock:
            tokens = self._by_key.get(event_key)
            if not tokens:
                return 0
            count = 0
            for token in list(tokens):
                pending = self._pending.get(token)
                if pending is None or pending.signaled:
                    continue
                pending.payload = payload
                pending.event.set()
                count += 1
            return count

    def signal_token(self, token: str, payload: dict) -> None:
        """直投：未知/已取走 token → WaitTokenNotFound；已 signaled → WaitAlreadySignaled。"""
        with self._lock:
            pending = self._pending.get(token)
            if pending is None:
                raise WaitTokenNotFound(token)
            if pending.signaled:
                raise WaitAlreadySignaled(token)
            pending.payload = payload
            pending.event.set()

    def list_pending(self) -> list[dict]:
        with self._lock:
            return [
                {
                    "token": p.token,
                    "eventKey": p.event_key,
                    "nodeId": p.node_id,
                    "graphId": p.graph_id,
                    "timeoutSeconds": p.timeout_seconds,
                    "deadlineAt": time.strftime(
                        "%Y-%m-%dT%H:%M:%SZ",
                        time.gmtime(time.time() + max(0.0, p.deadline - time.monotonic())),
                    ),
                }
                for p in self._pending.values()
            ]

    def reset(self) -> None:
        with self._lock:
            for pending in self._pending.values():
                pending.event.set()
            self._pending.clear()
            self._by_key.clear()

    def _remove_locked(self, pending: _Pending) -> None:
        self._pending.pop(pending.token, None)
        tokens = self._by_key.get(pending.event_key)
        if tokens is not None:
            tokens.discard(pending.token)
            if not tokens:
                self._by_key.pop(pending.event_key, None)
