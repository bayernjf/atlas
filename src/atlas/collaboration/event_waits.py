"""进程内事件等待 broker（wait 节点 waitType=event，docs/47；04 §5.5；多事件竞速 docs/54；AND 竞速/信号排队 docs/55）。

wait 执行器按渲染后的 event_key 登记 pending（token=``wait-``+uuid4），随后在
Starlette 线程池工作线程上阻塞；信号经登录态 REST 按 key 广播或按 token 直投，
broker 以 monotonic deadline 判定超时。等待以 0.2s 切片轮询 ``is_cancelled``，
支持协作式取消（RunCancelled，节点边界语义）。

docs/54 多事件竞速（OR）：一个 pending 可挂多个 event_key（``request_any``/
``eventKeys`` 1-8 个），任一键首决信号即唤醒，payload 注入 ``matchedEventKey``，
其余键的订阅在唤醒/超时/取消时一并清理；``request(event_key=)`` 保留为单键薄封装。

docs/55 AND 竞速（``eventWaitMode="all"``）：多键必须**全部**命中才放行，逐键
首条信号记入 ``matched``（重复同键不覆盖），放行 payload 携带按登记序排列的
``matchedEventKeys``、各键 ``matchedPayloads`` 与末集齐键 ``matchedEventKey``；
超时输出由 loader 附 ``receivedKeys``。人工直投（signal_token）不区分 mode，
直接放行（显式人工强制）。

docs/55 停摆期信号排队：广播键上没有任何有效 pending 时，信号进入 per-key 进程内
ring（``_queue``，容量 4/键、TTL 3600s），登记/恢复 pending 后立即消费（any 取
一键首条即命中、all 逐键填充）。队列只在同一进程生命周期内缓冲早到信号，重启窗口
内到达的信号仍丢失（跨重启/多实例排队缓做，docs/14 D19）。

每租户一个、挂 TenantServices（内存/PG 两档均为内存实例，同 cancellation_broker）；
进程内、重启即失、不支持多实例；PG 后端的跨重启恢复经 `restore()`（docs/53），
多键 pending 的帧内 ``eventKeys`` 与 mode 同样经 restore 重建（AND 已命中集合不跨
重启，恢复后须重新集齐）。
"""

from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field

_POLL_SLICE_SECONDS = 0.2

# docs/55：停摆期信号排队 ring（每键容量、有效期秒，monotonic 时间戳惰性过期）。
QUEUE_PER_KEY = 4
QUEUE_TTL_SECONDS = 3600.0


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
    mode: str = "any"  # docs/55：any=OR 首决（默认），all=AND 全命中
    event: threading.Event = field(default_factory=threading.Event)
    payload: dict | None = None
    # docs/55 AND：已命中键 → 该键信号 payload（dict 保序＝命中顺序）。
    matched: dict[str, dict] = field(default_factory=dict)

    @property
    def event_key(self) -> str:
        """首键（向后兼容单键形状：帧 eventKey、list_pending eventKey 均取首键）。"""
        return self.event_keys[0]

    @property
    def signaled(self) -> bool:
        return self.event.is_set()

    def received_keys(self) -> list[str]:
        """按登记序返回已命中的键（docs/55 all 超时输出 receivedKeys）。"""
        return [key for key in self.event_keys if key in self.matched]


@dataclass
class EventWaitBroker:
    """event_key → pending 集合的进程内等待/信号；全部方法线程安全。"""

    _pending: dict[str, _Pending] = field(default_factory=dict)
    _by_key: dict[str, set[str]] = field(default_factory=dict)
    _queue: dict[str, deque] = field(default_factory=dict)
    # docs/55：AND 超时移除前留存已命中键，供 loader 组装 receivedKeys（取后即删）。
    _last_received: dict[str, list[str]] = field(default_factory=dict)
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
        mode: str = "any",
    ) -> str:
        """登记等待并返回 token。

        ``mode="any"``（docs/54，默认）：OR 竞速，每个键都挂同一 token，任一键
        首决即唤醒。``mode="all"``（docs/55）：AND 竞速，全部键各收到一条信号才
        唤醒。``event_keys`` 调用方须已校验非空、≤8、元素合法且去重；此处防御性
        去重保序。登记后立即消费排队信号（缓解信号早到）。
        """
        keys = list(dict.fromkeys(event_keys))
        if not keys:
            raise ValueError("request_any 需要至少一个 event_key")
        if mode not in ("any", "all"):
            raise ValueError(f"非法 eventWaitMode：{mode}")
        token = f"wait-{uuid.uuid4().hex}"
        pending = _Pending(
            token=token,
            event_keys=keys,
            node_id=node_id,
            graph_id=graph_id,
            deadline=time.monotonic() + timeout_seconds,
            timeout_seconds=timeout_seconds,
            mode=mode,
        )
        with self._lock:
            self._pending[token] = pending
            for key in keys:
                self._by_key.setdefault(key, set()).add(token)
            self._drain_queue_locked(pending)
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
        mode: str = "any",
    ) -> None:
        """启动恢复（docs/53；docs/54 多键；docs/55 mode）：以中断帧重建 pending，deadline 只计剩余。

        幂等：token 已存在（含已 signaled）时 no-op，不覆盖在途状态。
        多键传 ``event_keys``；单键可仅传 ``event_key``（缺省退化为 [event_key]）。
        AND 的已命中集合不跨重启（v1，docs/55 §1），恢复后须重新集齐；同样消费
        本进程内排队信号。
        """
        keys = list(dict.fromkeys(event_keys or ([event_key] if event_key else [])))
        if not keys:
            raise ValueError("restore 需要 event_key 或 event_keys")
        if mode not in ("any", "all"):
            raise ValueError(f"非法 eventWaitMode：{mode}")
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
                mode=mode,
            )
            self._pending[token] = pending
            for key in keys:
                self._by_key.setdefault(key, set()).add(token)
            self._drain_queue_locked(pending)

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
                    if pending.mode == "all":
                        # docs/55：AND 超时留存已命中键，供 loader 输出 receivedKeys。
                        self._last_received[token] = pending.received_keys()
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

    def signal_key(self, event_key: str, payload: dict) -> dict:
        """广播：对挂该键的有效 pending 记信号，返回 ``{"released": n, "queued": bool}``。

        - any（OR，docs/54）：命中即唤醒；payload 未显式带 matchedEventKey 时注入
          本次命中的键；已被另一键首决的 pending 不覆盖（skip）。
        - all（AND，docs/55）：逐键记首条信号（重复同键不覆盖），全部键集齐才唤醒，
          放行 payload 携带 matchedEventKeys/matchedPayloads/matchedEventKey。
        - 该键没有任何有效 pending 时入排队 ring（docs/55），queued=True。
        """
        with self._lock:
            tokens = self._by_key.get(event_key)
            released = 0
            consumed = False  # 是否有有效 pending 首次接收了该键（AND 部分命中也算消费）
            if tokens:
                for token in list(tokens):
                    pending = self._pending.get(token)
                    if pending is None or pending.signaled:
                        continue
                    effective = dict(payload or {})
                    if pending.mode == "all":
                        if event_key in pending.matched:
                            # 同键重复信号：首条已消费，本次不计数（落到入队分支）。
                            continue
                        effective.setdefault("matchedEventKey", event_key)
                        pending.matched[event_key] = effective
                        consumed = True
                        if set(pending.matched) >= set(pending.event_keys):
                            pending.payload = self._build_all_payload(pending, event_key)
                            pending.event.set()
                            released += 1
                    else:
                        effective.setdefault("matchedEventKey", event_key)
                        pending.payload = effective
                        pending.event.set()
                        consumed = True
                        released += 1
            # 仅当没有任何 pending 首次接收（无消费者 / 全部已决 / AND 同键重复）才排队。
            queued = False
            if released == 0 and not consumed:
                queued = self._enqueue_locked(event_key, payload)
            return {"released": released, "queued": queued}

    def signal_token(self, token: str, payload: dict) -> None:
        """直投：未知/已取走 token → WaitTokenNotFound；已 signaled → WaitAlreadySignaled。

        人工显式放行，不区分 any/all 模式直接唤醒；payload 未带 matchedEventKey
        则补首键，保证等待输出总能得到命中键（docs/54）。
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

    def take_last_received(self, token: str) -> list[str]:
        """取出并清除某 token 上一次 AND 超时留存的已命中键（docs/55）；无则空表。"""
        with self._lock:
            return self._last_received.pop(token, [])

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
                if p.mode == "all":
                    row["eventWaitMode"] = "all"
                    row["receivedKeys"] = p.received_keys()
                rows.append(row)
            return rows

    def reset(self) -> None:
        with self._lock:
            for pending in self._pending.values():
                pending.event.set()
            self._pending.clear()
            self._by_key.clear()
            self._queue.clear()
            self._last_received.clear()

    # ---------------- 内部（持锁调用） ----------------

    @staticmethod
    def _build_all_payload(pending: _Pending, last_key: str) -> dict:
        """docs/55：AND 集齐时的放行 payload（按登记序的命中键 + 各键 payload）。"""
        ordered_keys = [key for key in pending.event_keys if key in pending.matched]
        return {
            "matchedEventKey": last_key,
            "matchedEventKeys": ordered_keys,
            "matchedPayloads": {key: pending.matched[key] for key in ordered_keys},
        }

    def _purge_queue_locked(self, key: str) -> deque:
        """取某键队列并惰性丢弃过期项；无有效项返回空 deque。"""
        queue = self._queue.get(key)
        if queue is None:
            return deque()
        now_mono = time.monotonic()
        while queue and now_mono - queue[0][0] > QUEUE_TTL_SECONDS:
            queue.popleft()
        if not queue:
            self._queue.pop(key, None)
            return deque()
        return queue

    def _enqueue_locked(self, event_key: str, payload: dict) -> bool:
        """无有效消费者时把信号按键入 ring（超容淘汰最旧）；返回是否入队。"""
        queue = self._queue.setdefault(event_key, deque(maxlen=QUEUE_PER_KEY))
        queue.append((time.monotonic(), dict(payload or {})))
        return True

    def _drain_queue_locked(self, pending: _Pending) -> None:
        """登记/恢复后消费排队信号：any 取任一键最早一条即命中；all 逐键填充首条。

        每键最多消费一条，余项保留给后续 pending；消费即出队。any 命中或 all
        集齐时放行（set）。
        """
        if pending.mode == "all":
            for key in pending.event_keys:
                if key in pending.matched:
                    continue
                queue = self._purge_queue_locked(key)
                if queue:
                    _ts, queued_payload = queue.popleft()
                    drained = dict(queued_payload)
                    drained.setdefault("matchedEventKey", key)
                    pending.matched[key] = drained
            if set(pending.matched) >= set(pending.event_keys):
                pending.payload = self._build_all_payload(pending, pending.event_keys[-1])
                pending.event.set()
            return
        # any：按登记序找第一个有排队信号的键，取最早一条命中。
        for key in pending.event_keys:
            queue = self._purge_queue_locked(key)
            if queue:
                _ts, queued_payload = queue.popleft()
                effective = dict(queued_payload)
                effective.setdefault("matchedEventKey", key)
                pending.payload = effective
                pending.event.set()
                return

    def _remove_locked(self, pending: _Pending) -> None:
        """从 token 表与该 pending 订阅的全部键索引摘除（docs/54 余键清理）。"""
        self._pending.pop(pending.token, None)
        for key in pending.event_keys:
            tokens = self._by_key.get(key)
            if tokens is not None:
                tokens.discard(pending.token)
                if not tokens:
                    self._by_key.pop(key, None)
