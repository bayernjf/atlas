"""审计日志（docs/35 §6，T6；docs/34 P1 #8）。

只记录**写操作的元数据**：谁（actor）在何时（at，UTC ISO）对什么路由模板（action，
如 ``POST /api/graphs/{graph_id}/runs``）做了动作、结果状态码（status_code）、
实际路径（path）、来源 IP。

硬约束（安全/隐私）：
- **绝不**记录请求体、查询参数、Authorization 头、密码、token、密钥信封等任何载荷；
- action 用路由**模板**（含 ``{param}`` 占位），避免把资源标识里的敏感串当成动作名；
- demo reset **不**清空审计（审计是安全痕迹，不随演示数据重置）；
- v1 为进程内 ring（每租户，默认 2000 条）＋ PG 持久化两档；前端审计页/SIEM 对接缓做。
"""

from __future__ import annotations

import json
import os
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel

# 只审计这些方法的 /api/ 请求（读操作不记，降噪）。
AUDITED_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
DEFAULT_RING_SIZE = 2000
RING_SIZE_ENV = "ATLAS_AUDIT_RING"
# 登录端点由端点内显式记录（仅成功记一条、失败不记），HTTP 中间件跳过，避免重复/漏记。
LOGIN_PATH = "/api/auth/login"


def ring_size_from_env() -> int:
    """读取 ATLAS_AUDIT_RING（1–100000，非法值回退默认 2000，fail-open 不阻断启动）。"""
    raw = os.environ.get(RING_SIZE_ENV, "").strip()
    if not raw:
        return DEFAULT_RING_SIZE
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_RING_SIZE
    if value < 1 or value > 100_000:
        return DEFAULT_RING_SIZE
    return value


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AuditEvent(BaseModel):
    """单条审计事件（仅元数据，无任何请求载荷/凭据）。"""

    id: str
    tenantId: str
    actor: str  # 用户名；未认证为 "anonymous"
    action: str  # "METHOD /route/template"
    statusCode: int
    path: str  # 实际路径（不含 query）
    ip: str
    at: str  # UTC ISO-8601


@runtime_checkable
class AuditRepository(Protocol):
    def record(
        self,
        *,
        tenant_id: str,
        actor: str,
        action: str,
        status_code: int,
        path: str,
        ip: str,
    ) -> dict[str, Any]: ...

    def list(self, *, limit: int = 100, action_prefix: str | None = None) -> list[dict[str, Any]]: ...

    def export_jsonl(self, *, action_prefix: str | None = None) -> str: ...

    def clear(self) -> None: ...


class AuditStore:
    """进程内 ring 审计存储（每租户一个实例；单锁；最旧溢出丢弃）。"""

    def __init__(self, maxlen: int | None = None) -> None:
        self._items: deque[AuditEvent] = deque(maxlen=maxlen or ring_size_from_env())
        self._counter = 0
        self._lock = threading.Lock()

    def record(
        self,
        *,
        tenant_id: str,
        actor: str,
        action: str,
        status_code: int,
        path: str,
        ip: str,
    ) -> dict[str, Any]:
        with self._lock:
            self._counter += 1
            event = AuditEvent(
                id=f"aud-{self._counter}",
                tenantId=tenant_id,
                actor=actor or "anonymous",
                action=action,
                statusCode=int(status_code),
                path=path,
                ip=ip or "",
                at=now_iso(),
            )
            self._items.append(event)
        return event.model_dump()

    @staticmethod
    def _matches(event: AuditEvent, action_prefix: str | None) -> bool:
        return action_prefix is None or event.action.startswith(action_prefix)

    def list(self, *, limit: int = 100, action_prefix: str | None = None) -> list[dict[str, Any]]:
        """倒序（最新在前），可按 action 前缀过滤；端点层负责把 limit clamp 到 1–500。"""
        bounded = max(1, min(int(limit), 500))
        with self._lock:
            matched = [e for e in reversed(self._items) if self._matches(e, action_prefix)]
            return [e.model_dump() for e in matched[:bounded]]

    def export_jsonl(self, *, action_prefix: str | None = None) -> str:
        """正序（旧→新，符合日志归档习惯）的 JSONL；每行一个事件。"""
        with self._lock:
            matched = [e for e in self._items if self._matches(e, action_prefix)]
            return "\n".join(
                json.dumps(e.model_dump(), ensure_ascii=False) for e in matched
            )

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
