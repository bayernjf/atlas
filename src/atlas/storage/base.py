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
from atlas.monitoring.silences import OnCallSchedule, Silence
from atlas.recording.cases import RecordingCase, RecordStep

if TYPE_CHECKING:
    # 仅类型标注用；运行时避免 import iam（iam.__init__ → deps → registry → storage 会成环）
    from atlas.iam.principals import Principal
    from atlas.connections.models import Connection

# ---- reset 分档（docs/24 §1.2③；/api/demo/reset 语义，12 §5） ----
RESET_RESETTABLE = "resettable"  # graph/approval/debug/monitoring/session/message/memory
RESET_PERSISTENT = "persistent"  # recording/feedback（测试资产，reset 不清除）


class StorageError(Exception):
    """存储层统一错误。M5a 进程内实现不抛；M5b PG 实现据此归一。"""


@runtime_checkable
class GraphRepository(Protocol):
    def save(self, raw: dict[str, Any]) -> str: ...
    def update_draft(self, graph_id: str, raw: dict[str, Any]) -> None: ...
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
        graph_id: str = "",
        subgraphs: dict[str, dict[str, Any]] | None = None,
        recorded_at: str | None = None,
    ) -> RecordingCase: ...
    def update_meta(
        self,
        case_id: str,
        *,
        name: str | None = None,
        inputs: dict[str, Any] | None = None,
    ) -> RecordingCase | None: ...
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
        notify_recipients: list[str] | None = None,
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
        notify_recipients: list[str] | None = None,
    ) -> str: ...
    def wait(self, token: str) -> Decision | None: ...
    def resolve(
        self, token: str, decision: Decision, *, resolved_by: str = "human", comment: str = ""
    ) -> bool: ...
    def complete_timeout(self, token: str, decision: Decision) -> tuple[Decision, str] | None: ...
    def get(self, token: str) -> dict | None: ...
    def list_pending(self) -> list[dict]: ...
    def list_decided(self, limit: int = 50) -> list[dict]: ...
    def get_notify_recipients(self, token: str) -> list[str]: ...
    def reset(self) -> None: ...


@runtime_checkable
class DebugRepository(Protocol):
    """DebuggerBroker 的挂起-决断语义（token + Event + step/continue/stop 状态机）。"""

    def create(self, *, graph_id: str, breakpoints: list[dict[str, Any]] | None) -> DebugSession: ...
    def get_session(self, token: str) -> DebugSession | None: ...
    def list_pending(self) -> list[dict[str, Any]]: ...
    def reset(self) -> None: ...


@runtime_checkable
class RunRepository(Protocol):
    """运行生命周期状态（running/suspended/completed/failed/interrupted，docs/24 §3.3/§4）。

    M5b 新增：`/api/runs` 查询与 SSE 断线重连据此工作；跨租户/不存在返回 None 对齐 404。
    """

    def begin(self, *, run_id: str, graph_id: str, mode: str) -> None: ...
    def suspend(
        self,
        *,
        run_id: str,
        node_id: str,
        kind: str,
        resume_token: str,
        deadline_at: str | None,
    ) -> None: ...
    def finish(
        self,
        *,
        run_id: str,
        status: Literal["completed", "failed", "interrupted", "cancelled"],
        error: str | None = None,
        outputs: dict[str, Any] | None = None,
        trace: list[str] | None = None,
    ) -> None: ...
    def get(self, run_id: str) -> dict | None: ...
    def list(self, status: str | None = None, limit: int = 50) -> list[dict]: ...
    def reset(self) -> None: ...


@runtime_checkable
class MonitoringRepository(Protocol):
    def record_run(
        self,
        *,
        graph_id: str,
        mode: Literal["sync", "stream"],
        status: Literal["completed", "error", "cancelled"],
        started_at: str,
        duration_ms: float,
        nodes: list,
        error: str | None = None,
        trace_id: str = "",
        resolved_version: int | None = None,
        business=None,
        tool_calls: list | None = None,
        spans: dict | None = None,
    ) -> RunRecord: ...
    def list_runs(self, graph_id: str | None = None, limit: int = 50) -> list[RunRecord]: ...
    def get_run(self, run_id: str) -> RunRecord | None: ...
    def list_alerts(self, status: str | None = None) -> list[Alert]: ...
    def get_alert(self, alert_id: str) -> Alert | None: ...
    def acknowledge_alert(self, alert_id: str) -> Alert | Literal[False] | None: ...
    def resolve_alert(self, alert_id: str) -> Alert | Literal[False] | None: ...
    def get_rules(self) -> RuleConfig: ...
    def update_rules(self, raw: dict) -> RuleConfig: ...
    def snapshot_metrics(self) -> dict: ...
    # docs/33 §5：静默 / 值班（进程内）
    def create_silence(
        self, *, rule_id: str | None, graph_id: str | None, duration_minutes: int,
        reason: str, created_by: str,
    ) -> Silence: ...
    def list_silences(self, active: bool | None = None) -> list[Silence]: ...
    def delete_silence(self, silence_id: str) -> bool: ...
    def get_oncall(self) -> OnCallSchedule: ...
    def set_oncall(self, *, members: list[str], updated_by: str) -> OnCallSchedule: ...
    def rotate_oncall(self, *, updated_by: str) -> OnCallSchedule: ...
    def reset(self) -> None: ...


@runtime_checkable
class MemoryRepository(Protocol):
    """长期记忆（M11，docs/26 §4.1）：fact/preference 写入与语义检索。

    租户分区是构造期关切（不进签名）；返回的 MemoryItem dict 不含 embedding；
    归 RESET_RESETTABLE（记忆是演示运行数据，reset_tenant 清空）。
    """

    def remember(
        self,
        *,
        kind: str,  # "fact" | "preference"
        content: str,
        scope: dict[str, str] | None = None,
        confidence: float = 1.0,
        source: str = "tool",
        metadata: dict[str, str] | None = None,
    ) -> dict[str, Any]: ...

    def recall(
        self,
        query: str,
        *,
        kind: str | None = None,
        scope: dict[str, str] | None = None,  # 子集匹配：item.scope 须包含其全部键值
        top_k: int = 5,
        min_score: float = 0.0,
    ) -> list[dict[str, Any]]: ...  # 每项 = MemoryItem dict + "score"，按 score 降序

    def list(self, *, kind: str | None = None, limit: int = 50) -> list[dict[str, Any]]: ...
    def update(
        self, memory_id: str, **fields: Any
    ) -> dict[str, Any] | None: ...  # docs/28 §5.1 手动编辑白名单字段；不存在返回 None（跨租户同不存在）
    def delete(self, memory_id: str) -> bool: ...  # 不存在返回 False（跨租户同不存在）
    def clear(self) -> None: ...


@runtime_checkable
class ConnectionRepository(Protocol):
    """OAuth2 连接配置（docs/35 §4，T4；generic OAuth2，平台无关）。

    存 Connection 对象，秘密字段为 SecretProvider 信封；租户分区是构造期关切
    （PgConnectionStore 行内 tenant_id 过滤）。连接属租户配置+凭据，
    demo reset 不清除（与审计/录制一致）。
    """

    def create(self, conn: "Connection") -> "Connection": ...
    def get(self, conn_id: str) -> "Connection | None": ...
    def list(self) -> list["Connection"]: ...
    def save(self, conn: "Connection") -> "Connection": ...
    def delete(self, conn_id: str) -> bool: ...
    def reset(self) -> None: ...
