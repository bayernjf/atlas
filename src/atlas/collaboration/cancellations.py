"""进程内运行取消信号（协作式急停，docs/27 §4.1）。

执行器在 run 启动时按 run_id 登记一个 threading.Event，急停端点置位；
run_graph 经 ``is_cancelled`` 零参回调在**节点边界**（node_start 之后、节点逻辑之前）
轮询，命中抛 RunCancelled 协作式终止。不在 wait/approval/tool 阻塞中点强杀，
长节点执行完毕后于下一节点边界生效。

每租户一个、挂 TenantServices（照 approval/debug broker）；进程内、重启即失，
不支持跨实例；持久化中断/多实例急停缓做 docs/14 D27。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field


class RunCancelled(Exception):
    """协作式取消：在节点边界抛出以终止本次 run（语义非异常失败）。"""

    def __init__(self, node_id: str):
        super().__init__(f"run cancelled at {node_id}")
        self.node_id = node_id


@dataclass
class RunCancellationBroker:
    """run_id → 取消事件；register/cancel/is_set/unregister 均线程安全。"""

    _events: dict[str, threading.Event] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def register(self, run_id: str) -> threading.Event:
        """worker 启动前登记；返回该 run 专属事件（调用方可闭包持有，注销后仍可读）。"""
        event = threading.Event()
        with self._lock:
            # 幂等：极端重入下不覆盖既有事件。
            existing = self._events.get(run_id)
            if existing is not None:
                return existing
            self._events[run_id] = event
        return event

    def cancel(self, run_id: str) -> bool:
        """置位取消。返回 True=存在注册句柄（运行中，含已置位的重复取消，幂等 200）；
        返回 False=无句柄（run 已结束注销或未知，调用方据此区分 409/404）。"""
        with self._lock:
            event = self._events.get(run_id)
            if event is None:
                return False
            event.set()
            return True

    def unregister(self, run_id: str) -> None:
        """worker 结束（完成/失败/取消）后注销句柄；此后 cancel 返回 False。"""
        with self._lock:
            self._events.pop(run_id, None)

    def reset(self) -> None:
        """Demo reset：置位释放所有在跑 run 并清空。"""
        with self._lock:
            for event in self._events.values():
                event.set()
            self._events.clear()
