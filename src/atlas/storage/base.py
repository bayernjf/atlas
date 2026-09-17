"""M5a 存储抽象：按资源分组的 Repository Protocol 与统一约定（docs/24 §1，08 M5a 立项条）。

结构化类型（`typing.Protocol`）：实现类不强制继承，只要方法集与签名赋值兼容即可；
进程内实现见 `memory.py`（默认/测试后端），PG 实现随 M5b。

统一约定（docs/24 §1.2）：
- 租户分区是**构造期关切**，不进方法签名——统一经 `TenantRegistry.get`（for_tenant 语义）取用；
- reset 分 resettable / persistent 两档（见 RESET_* 常量）；
- id 由实现内部生成（进程内 `graph-N` 计数器，PG 换序列、对外格式不变）；
- 时间统一 UTC ISO-8601 字符串。

docs/24 §1 的「InterruptionRepository（审批+调试挂起-决断语义）」在落码时拆为
`ApprovalRepository` 与 `DebugRepository` 两个 Protocol：二者是两个独立实现类
（ApprovalBroker / DebuggerBroker），没有单一类同时满足合并方法集；二者共享的
token+Event+首决生效语义在 M5b 统一落库为 interruption_frame（docs/24 §2.3）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

from atlas.collaboration.approvals import Decision
from atlas.debug.sessions import DebugSession
from atlas.monitoring.records import Alert, RuleConfig, RunRecord
from atlas.recording.cases import RecordingCase, RecordStep

if TYPE_CHECKING:
    # 仅类型标注用；运行时避免 import iam（iam.__init__ → deps → registry → storage 会成环）
    from atlas.iam.principals import Principal

# ---- reset 分档（docs/24 §1.2③；/api/demo/reset 语义，12 §5） ----
RESET_RESETTABLE = "resettable"  # graph/approval/debug/monitoring/session/message
RESET_PERSISTENT = "persistent"  # recording/feedback（测试资产，reset 不清除）


class StorageError(Exception):
    """存储层统一错误。M5a 进程内实现不抛；M5b PG 实现据此归一。"""


@runtime_checkable
class GraphRepository(Protocol):
    def save(self, raw: dict[str, Any]) -> str: ...
    def get(self, graph_id: str, release_version: int | None = None) -> dict[str, Any] | None: ...
    def list(self) -> list[dict[str, Any]]: ...
    # M6 版本化（docs/20 §4.1 / ADR T19）：发布冻结不可变版本，releaseVersion 从 1 递增
    def publish(self, graph_id: str, raw: dict[str, Any]) -> int: ...
    def list_versions(self, graph_id: str) -> list[int]: ...
    def clear(self) -> None: ...


@runtime_checkable
class RecordingRepository(Protocol):
    def add(
        self,
        *,
        name: str,
        graph: dict[str, Any],
        inputs: dict[str, Any] | None,
        steps: list[RecordStep],
        status: str,
    ) -> RecordingCase: ...
    def list(self) -> list[RecordingCase]: ...
    def get(self, case_id: str) -> RecordingCase | None: ...
    def delete(self, case_id: str) -> bool: ...


@runtime_checkable
class FeedbackRepository(Protocol):
    def add(self, request: Any) -> dict[str, Any]: ...
    def list(self) -> list[dict[str, Any]]: ...


@runtime_checkable
class SessionRepository(Protocol):
    def issue(self, principal: "Principal") -> str: ...
    def principal_for_token(self, token: str | None) -> "Principal | None": ...
    def revoke(self, token: str) -> None: ...
    def reset(self) -> None: ...


@runtime_checkable
class ApprovalRepository(Protocol):
    """ApprovalBroker 的挂起-决断语义（token + Event + 首决生效）。"""

    def request(
        self,
        *,
        node_id: str,
        graph_id: str,
        summary: str,
        approver: str,
        timeout_seconds: int,
    ) -> str: ...
    def restore(
        self,
        *,
        token: str,
        node_id: str,
        graph_id: str,
        summary: str,
        approver: str,
        remaining_seconds: float,
    ) -> str: ...
    def wait(self, token: str) -> Decision | None: ...
    def resolve(
        self, token: str, decision: Decision, *, resolved_by: str = "human", comment: str = ""
    ) -> bool: ...
    def complete_timeout(self, token: str, decision: Decision) -> tuple[Decision, str] | None: ...
    def get(self, token: str) -> dict | None: ...
    def list_pending(self) -> list[dict]: ...
    def reset(self) -> None: ...


@runtime_checkable
class DebugRepository(Protocol):
    """DebuggerBroker 的挂起-决断语义（token + Event + step/continue/stop 状态机）。"""

    def create(self, *, graph_id: str, breakpoints: list[dict[str, Any]] | None) -> DebugSession: ...
    def get_session(self, token: str) -> DebugSession | None: ...
    def list_pending(self) -> list[dict[str, Any]]: ...
    def reset(self) -> None: ...


@runtime_checkable
class MonitoringRepository(Protocol):
    def record_run(
        self,
        *,
        graph_id: str,
        mode: Literal["sync", "stream"],
        status: Literal["completed", "error"],
        started_at: str,
        duration_ms: float,
        nodes: list,
        error: str | None = None,
    ) -> RunRecord: ...
    def list_runs(self, graph_id: str | None = None, limit: int = 50) -> list[RunRecord]: ...
    def list_alerts(self, status: str | None = None) -> list[Alert]: ...
    def get_alert(self, alert_id: str) -> Alert | None: ...
    def acknowledge_alert(self, alert_id: str) -> Alert | Literal[False] | None: ...
    def resolve_alert(self, alert_id: str) -> Alert | Literal[False] | None: ...
    def get_rules(self) -> RuleConfig: ...
    def update_rules(self, raw: dict) -> RuleConfig: ...
    def snapshot_metrics(self) -> dict: ...
    def reset(self) -> None: ...
