"""进程内事件等待 broker（wait 节点 waitType=event，docs/47；04 §5.5；多事件竞速 docs/54）。

wait 执行器按渲染后的 event_key 登记 pending（token=``wait-``+uuid4），随后在
Starlette 线程池工作线程上阻塞；信号经登录态 REST 按 key 广播或按 token 直投，
broker 以 monotonic deadline 判定超时。等待以 0.2s 切片轮询 ``is_cancelled``，
支持协作式取消（RunCancelled，节点边界语义）。

docs/54 多事件竞速（OR）：一个 pending 可挂多个 event_key（``request_any``/
``eventKeys`` 1-8 个），任一键首决信号即唤醒，payload 注入 ``matchedEventKey``，
其余键的订阅在唤醒/超时/取消时一并清理；``request(event_key=)`` 保留为单键薄封装。

每租户一个、挂 TenantServices（内存/PG 两档均为内存实例，同 cancellation_broker）；
进程内、重启即失、不支持多实例；PG 后端的跨重启恢复经 `restore()`（docs/53），
多键 pending 的帧内 ``eventKeys`` 同样经 restore 重建。
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
    event_keys: list[str]
    node_id: str
    graph_id: str
    deadline: float
    timeout_seconds: int
    event: threading.Event = field(default_factory=threading.Event)
    payload: dict | None = None

    @property
    def event_key(self) -> str:
        """首键（向后兼容单键形状：帧 eventKey、list_pending eventKey 均取首键）。"""
        return self.event_keys[0]

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
        """单键登记（docs/47）；多事件竞速见 ``request_any``。"""
        return self.request_any(
            event_keys=[event_key],
            node_id=node_id,
            graph_id=graph_id,
            timeout_seconds=timeout_seconds,
        )

    def request_any(
        self,
        *,
        event_keys: list[str],
        node_id: str,
        graph_id: str,
        timeout_seconds: int,
    ) -> str:
        """多事件 OR 竞速登记（docs/54）：每个键都挂同一 token，任一键首决即唤醒。

        ``event_keys`` 调用方须已校验非空、≤8、元素合法且去重；此处防御性去重保序。
        """
        keys = list(dict.fromkeys(event_keys))
        if not keys:
            raise ValueError("request_any 需要至少一个 event_key")
        token = f"wait-{uuid.uuid4().hex}"
        pending = _Pending(
            token=token,
            event_keys=keys,
            node_id=node_id,
            graph_id=graph_id,
            deadline=time.monotonic() + timeout_seconds,
            timeout_seconds=timeout_seconds,
        )
        with self._lock:
            self._pending[token] = pending
            for key in keys:
                self._by_key.setdefault(key, set()).add(token)
        return token

    def restore(
        self,
        *,
        token: str,
        event_key: str | None = None,
        node_id: str,
        graph_id: str,
        timeout_seconds: float,
        event_keys: list[str] | None = None,
    ) -> None:
        """启动恢复（docs/53；docs/54 多键）：以中断帧重建 pending，deadline 只计剩余。

        幂等：token 已存在（含已 signaled）时 no-op，不覆盖在途状态。
        多键传 ``event_keys``；单键可仅传 ``event_key``（缺省退化为 [event_key]）。
        """
        keys = list(dict.fromkeys(event_keys or ([event_key] if event_key else [])))
        if not keys:
            raise ValueError("restore 需要 event_key 或 event_keys")
        with self._lock:
            if token in self._pending:
                return
            pending = _Pending(
                token=token,
                event_keys=keys,
                node_id=node_id,
                graph_id=graph_id,
                deadline=time.monotonic() + max(0.0, timeout_seconds),
                timeout_seconds=int(max(0.0, timeout_seconds)),
            )
            self._pending[token] = pending
            for key in keys:
                self._by_key.setdefault(key, set()).add(token)

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
        """广播：释放全部同 key pending，返回释放条目数（无 pending 返 0）。

        docs/54：竞速 pending 命中时，若 payload 未显式带 matchedEventKey，
        注入本次命中的键；已被另一键首决的 pending 不覆盖（skip）。
        """
        with self._lock:
            tokens = self._by_key.get(event_key)
            if not tokens:
                return 0
            count = 0
            for token in list(tokens):
                pending = self._pending.get(token)
                if pending is None or pending.signaled:
                    continue
                effective = dict(payload or {})
                effective.setdefault("matchedEventKey", event_key)
                pending.payload = effective
                pending.event.set()
                count += 1
            return count

    def signal_token(self, token: str, payload: dict) -> None:
        """直投：未知/已取走 token → WaitTokenNotFound；已 signaled → WaitAlreadySignaled。

        docs/54：竞速 pending 被直投时，payload 未带 matchedEventKey 则补首键，
        保证等待输出总能得到命中键。
        """
        with self._lock:
            pending = self._pending.get(token)
            if pending is None:
                raise WaitTokenNotFound(token)
            if pending.signaled:
                raise WaitAlreadySignaled(token)
            effective = dict(payload or {})
            effective.setdefault("matchedEventKey", pending.event_key)
            pending.payload = effective
            pending.event.set()

    def list_pending(self) -> list[dict]:
        with self._lock:
            rows = []
            for p in self._pending.values():
                row = {
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
                if len(p.event_keys) > 1:
                    row["eventKeys"] = list(p.event_keys)
                rows.append(row)
            return rows

    def reset(self) -> None:
        with self._lock:
            for pending in self._pending.values():
                pending.event.set()
            self._pending.clear()
            self._by_key.clear()

    def _remove_locked(self, pending: _Pending) -> None:
        """从 token 表与该 pending 订阅的全部键索引摘除（docs/54 余键清理）。"""
        self._pending.pop(pending.token, None)
        for key in pending.event_keys:
            tokens = self._by_key.get(key)
            if tokens is not None:
                tokens.discard(pending.token)
                if not tokens:
                    self._by_key.pop(key, None)
