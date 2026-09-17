"""进程内存储实现——八个 store 的聚合入口（M5a，docs/24 §1 / 08 M5a 立项条）。

GraphStore / FeedbackStore（及反馈入参模型 FeedbackRequest）原内联在
`api/main.py`，此处**搬移**为独立模块；其余六类经 re-export 聚合：
实现类体保留在原领域模块（搬移是物理移动、re-export 已是单一聚合入口，
行为等价），`TenantRegistry` 统一从此处构造，消除 `api/main.py → iam.registry`
的延迟导入环。进程内实现是默认/测试后端；PG 实现随 M5b。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

from atlas.collaboration.approvals import ApprovalBroker
from atlas.debug.sessions import DebuggerBroker
from atlas.monitoring.records import MonitoringStore
from atlas.recording.cases import RecordingStore


class FeedbackRequest(BaseModel):
    type: Literal["bug", "suggestion"]
    content: str = Field(min_length=1, max_length=2000)
    contact: str = Field(default="", max_length=200)


class GraphStore:
    def __init__(self) -> None:
        self._graphs: dict[str, dict[str, Any]] = {}
        self._updated_at: dict[str, str] = {}
        self._counter = 0
        # M6 版本化（docs/20 §4.1 / ADR T19）：graph_id -> {release_version: 冻结快照}
        self._versions: dict[str, dict[int, dict[str, Any]]] = {}
        self._version_counter: dict[str, int] = {}

    def save(self, raw: dict[str, Any]) -> str:
        self._counter += 1
        graph_id = f"graph-{self._counter}"
        self._graphs[graph_id] = raw
        self._updated_at[graph_id] = datetime.now(timezone.utc).isoformat()
        return graph_id

    def get(self, graph_id: str, release_version: int | None = None) -> dict[str, Any] | None:
        if release_version is None:
            return self._graphs.get(graph_id)
        return self._versions.get(graph_id, {}).get(release_version)

    def list(self) -> list[dict[str, Any]]:
        return [
            {
                "id": graph_id,
                "node_count": len(raw.get("nodes", [])),
                "updated_at": self._updated_at[graph_id],
            }
            for graph_id, raw in self._graphs.items()
        ]

    def publish(self, graph_id: str, raw: dict[str, Any]) -> int:
        """把给定快照存为下一个发布版本（releaseVersion 从 1 递增）；只允许发布已存在的草稿。

        快照带 `releaseVersion` 字段（ADR T19：发布产物带、草稿 latest 不带）。
        """
        if graph_id not in self._graphs:
            raise KeyError(graph_id)
        version = self._version_counter.get(graph_id, 0) + 1
        self._version_counter[graph_id] = version
        self._versions.setdefault(graph_id, {})[version] = {**raw, "releaseVersion": version}
        return version

    def list_versions(self, graph_id: str) -> list[int]:
        return sorted(self._versions.get(graph_id, {}).keys())

    def clear(self) -> None:
        self._graphs = {}
        self._updated_at = {}
        self._counter = 0
        self._versions = {}
        self._version_counter = {}


class FeedbackStore:
    """Phase 1 种子反馈：进程内存储（重启清空，与 Demo 同假设）；reset 不清除。"""

    def __init__(self) -> None:
        self._items: list[dict[str, Any]] = []
        self._counter = 0

    def add(self, request: FeedbackRequest) -> dict[str, Any]:
        self._counter += 1
        item = {
            "id": f"feedback-{self._counter}",
            "type": request.type,
            "content": request.content,
            "contact": request.contact,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._items.append(item)
        return item

    def list(self) -> list[dict[str, Any]]:
        return list(self._items)


# 六类领域 store 的聚合 re-export（实现类体在原模块，见模块 docstring）。
# SessionStore 是 iam 包内的**全局**会话单例（非租户 store，不进 TenantServices），
# 不在此聚合——若 re-export 会经 iam.__init__ → deps → registry 形成 import 环。
__all__ = [
    "FeedbackRequest",
    "FeedbackStore",
    "GraphStore",
    "ApprovalBroker",
    "DebuggerBroker",
    "MonitoringStore",
    "RecordingStore",
]
