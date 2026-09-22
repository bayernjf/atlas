"""进程内审批信号（human_approval 节点挂起源；契约 04 §5.6）。

执行器在 broker 登记 pending 请求后阻塞等待 threading.Event，
审批经 REST 端点 resolve 放行；超时由等待方自行判定并 resolve。
进程内单例、重启即失，不支持跨实例；持久化中断-恢复缓做 docs/14 D20。
"""

from __future__ import annotations

import copy
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

Decision = Literal["approved", "rejected"]


@dataclass
class _Pending:
    event: threading.Event
    summary: str
    approver: str
    timeout_seconds: float
    node_id: str
    graph_id: str
    created_at: float = 0.0
    decision: Decision | None = None
    resolved_by: str | None = None
    comment: str = ""
    # M8：命中交互卡片时携带卡片 id 与挂起时上下文快照（供三渠道渲染）；
    # action_id 为本次决策实际命中的卡片动作（预置/超时来源为 None）。
    card_template_id: str | None = None
    card_context: dict[str, Any] | None = None
    action_id: str | None = None


@dataclass
class ApprovalBroker:
    """token → pending 请求；request/resolve/wait 均线程安全。"""

    _pending: dict[str, _Pending] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def request(
        self,
        *,
        node_id: str,
        graph_id: str,
        summary: str,
        approver: str,
        timeout_seconds: int,
        card_template_id: str | None = None,
        card_context: dict[str, Any] | None = None,
    ) -> str:
        token = uuid.uuid4().hex
        with self._lock:
            self._pending[token] = _Pending(
                event=threading.Event(),
                summary=summary,
                approver=approver,
                timeout_seconds=timeout_seconds,
                node_id=node_id,
                graph_id=graph_id,
                created_at=time.time(),
                card_template_id=card_template_id,
                card_context=copy.deepcopy(card_context) if card_context is not None else None,
            )
        return token

    def restore(
        self,
        *,
        token: str,
        node_id: str,
        graph_id: str,
        summary: str,
        approver: str,
        remaining_seconds: float,
        card_template_id: str | None = None,
        card_context: dict[str, Any] | None = None,
    ) -> str:
        """恢复扫描器用：以帧内原 token 重建 pending（不生成新 token），剩余时长照扣。"""
        with self._lock:
            self._pending[token] = _Pending(
                event=threading.Event(),
                summary=summary,
                approver=approver,
                timeout_seconds=remaining_seconds,
                node_id=node_id,
                graph_id=graph_id,
                created_at=time.time(),
                card_template_id=card_template_id,
                card_context=copy.deepcopy(card_context) if card_context is not None else None,
            )
        return token

    def wait(self, token: str) -> Decision | None:
        """阻塞至决策到达或超时；返回 None 表示仍未决（调用方按 onTimeout 决策）。"""
        with self._lock:
            pending = self._pending.get(token)
        if pending is None:
            return None
        pending.event.wait(pending.timeout_seconds)
        return pending.decision

    def resolve(
        self,
        token: str,
        decision: Decision,
        *,
        resolved_by: str = "human",
        comment: str = "",
        action_id: str | None = None,
    ) -> bool:
        """首决生效：返回 True 表示本次调用完成决策，False 表示未知或已决。"""
        with self._lock:
            pending = self._pending.get(token)
            if pending is None or pending.decision is not None:
                return False
            pending.decision = decision
            pending.resolved_by = resolved_by
            pending.comment = comment
            if action_id is not None:
                pending.action_id = action_id
            pending.event.set()
        return True

    def complete_timeout(self, token: str, decision: Decision) -> tuple[Decision, str] | None:
        """wait 超时后调用；若等待期间决策恰好到达则返回既有决策，否则记超时决策。

        返回 (decision, resolved_by)；未知 token 返回 None。
        """
        with self._lock:
            pending = self._pending.get(token)
            if pending is None:
                return None
            if pending.decision is None:
                pending.decision = decision
                pending.resolved_by = "timeout"
                pending.event.set()
            return pending.decision, pending.resolved_by or "timeout"

    def get(self, token: str) -> dict | None:
        with self._lock:
            pending = self._pending.get(token)
            if pending is None:
                return None
            result = {
                "token": token,
                "node_id": pending.node_id,
                "graph_id": pending.graph_id,
                "summary": pending.summary,
                "approver": pending.approver,
                "timeoutSeconds": pending.timeout_seconds,
                "createdAt": pending.created_at,
                "decision": pending.decision,
                "resolvedBy": pending.resolved_by,
                "comment": pending.comment,
                "actionId": pending.action_id,
            }
            if pending.card_template_id:
                result["cardTemplateId"] = pending.card_template_id
            return result

    def get_card_context(self, token: str) -> dict[str, Any] | None:
        """命中卡片时返回挂起时上下文快照的深拷贝（渲染用）；未知 token/无卡返 None。"""
        with self._lock:
            pending = self._pending.get(token)
            if pending is None or pending.card_context is None:
                return None
            return copy.deepcopy(pending.card_context)

    def list_pending(self) -> list[dict]:
        with self._lock:
            tokens = [t for t, p in self._pending.items() if p.decision is None]
            return [self._public(t, self._pending[t]) for t in tokens]

    @staticmethod
    def _public(token: str, pending: _Pending) -> dict:
        result = {
            "token": token,
            "node_id": pending.node_id,
            "graph_id": pending.graph_id,
            "summary": pending.summary,
            "approver": pending.approver,
            "timeoutSeconds": pending.timeout_seconds,
            "createdAt": pending.created_at,
        }
        if pending.card_template_id:
            result["cardTemplateId"] = pending.card_template_id
        return result

    def reset(self) -> None:
        """Demo reset：释放所有等待方（按拒绝放行）并清空。"""
        with self._lock:
            for pending in self._pending.values():
                if pending.decision is None:
                    pending.decision = "rejected"
                    pending.resolved_by = "timeout"
                    pending.event.set()
            self._pending.clear()
