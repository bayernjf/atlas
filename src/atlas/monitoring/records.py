"""基础监控告警：运行记录与进程内存储（契约 04 §5.13，06 §6.11）。

MonitoringStore 为进程内单例：200 条 ring buffer + 单锁同步评估规则，
重启清空（持久化随 11 S1/14 D28）；/api/demo/reset 清空运行/告警并恢复默认规则。
debug 运行、录制回放、subgraph 重入不经两个真实运行入口，天然不入记录。
"""

from collections import deque
from datetime import datetime, timezone
from threading import Lock
from typing import Literal

from pydantic import BaseModel, Field

from .alerts import (
    Alert,
    AlertEvent,
    RuleConfig,
    apply_escalation,
    evaluate_rules,
    rules_from_raw,
    validate_rules,
)
from .silences import OnCallSchedule, OpsStore, Silence
from .notify import (
    AlertChannel,
    AlertChannelDelivery,
    alert_channel_from_raw,
    validate_alert_channel,
)
from .business import BusinessOutcome
from .metrics import NodeResult, is_healthy

RUN_RING_SIZE = 200


class ToolCallMetric(BaseModel):
    """单次工具（适配器能力）调用埋点（docs/28 §4.1 ⑧）。

    action_status：SIMULATED＝无 ``adapter/capability`` 或未注册 registry 的本地构造
    （照记但不纳延迟分位）；SUCCESS/FAILED 取适配器 ActionResult 归一。
    """

    node_id: str
    tool: str
    duration_ms: float
    action_status: Literal["SUCCESS", "FAILED", "SIMULATED"]
    error_code: str | None = None


class RunRecord(BaseModel):
    id: str
    graph_id: str
    mode: Literal["sync", "stream"]
    status: Literal["completed", "error", "cancelled"]
    started_at: str
    finished_at: str
    duration_ms: float
    nodes: list[NodeResult]
    error: str | None = None
    trace_id: str = ""  # M10：本 run 的 traceId（可空，向后兼容；debug/回放/subgraph 重入不写）
    resolved_version: int | None = None  # M9：入站 event 经 Router 解析钉住的发布版本（手动运行/草稿为 None）
    business: BusinessOutcome | None = None  # M9：业务结果（退款/人工升级/金额差异）；无业务结果为 None
    tool_calls: list[ToolCallMetric] = Field(default_factory=list)  # docs/28 §4.1：顶层工具调用埋点（子图/mock/debug/回放不采）
    spans: dict | None = None  # docs/33 §4：tracer.to_tree() 根 span（trace 端点懒加载；列表 API 不返；历史/debug/回放为 None）


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class MonitoringStore:
    def __init__(self) -> None:
        self._lock = Lock()
        self._runs: deque[RunRecord] = deque(maxlen=RUN_RING_SIZE)
        self._alerts: list[Alert] = []
        self._streaks: dict[str, int] = {}
        self._rules = RuleConfig()
        self._ops = OpsStore()
        self._channel = AlertChannel()
        self._channel_delivery = AlertChannelDelivery()
        self._notifier = None
        self._run_counter = 0
        self._alert_counter = 0

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
        business: BusinessOutcome | None = None,
        tool_calls: list | None = None,
        spans: dict | None = None,
    ) -> RunRecord:
        pending: list[tuple[Alert, AlertChannel]] = []
        with self._lock:
            self._run_counter += 1
            record = RunRecord(
                id=f"run-{self._run_counter}",
                graph_id=graph_id,
                mode=mode,
                status=status,
                started_at=started_at,
                finished_at=_now_iso(),
                duration_ms=duration_ms,
                nodes=nodes,
                error=error,
                trace_id=trace_id,
                resolved_version=resolved_version,
                business=business,
                tool_calls=tool_calls or [],
                spans=spans,
            )
            self._runs.append(record)
            healthy = is_healthy(record)
            streak = 0 if healthy else self._streaks.get(graph_id, 0) + 1
            self._streaks[graph_id] = streak
            recent_by_graph = [run for run in self._runs if run.graph_id == graph_id]
            events = evaluate_rules(
                record=record,
                healthy=healthy,
                recent_by_graph=recent_by_graph,
                streak=streak,
                rules=self._rules,
            )
            for event in events:
                # docs/33 §5.1：命中活跃静默则不新建/不合并/不升级，仅累加压下计数
                if self._ops.suppress_if_matched(event.rule_id, record.graph_id):
                    continue
                raised = self._raise_or_merge(event=event, record=record)
                if raised is not None:
                    alert, transition = raised
                    pending.append(
                        (alert, self._channel.model_copy(deep=True), transition)
                    )
            # docs/54 §6：本次运行健康即自动恢复该图仍 open 的内置告警（一次健康即恢复，
            # 不做连续 N 次/flapping 抑制）；acknowledged 与 rollout_gate 不自动恢复，
            # 复用 last_seen/last_run_id、不新增字段（PG 兼容），锁外发 recovery 通知。
            if healthy:
                for alert in self._alerts:
                    if (
                        alert.graph_id == record.graph_id
                        and alert.status == "open"
                        and alert.rule_id != "rollout_gate"
                    ):
                        alert.status = "resolved"
                        alert.last_seen = record.finished_at
                        alert.last_run_id = record.id
                        pending.append(
                            (alert, self._channel.model_copy(deep=True), "recovery")
                        )
        for alert, cfg, transition in pending:
            self._notify_outside_lock(alert, cfg, transition=transition)
        return record

    def _notify_outside_lock(
        self, alert: Alert, cfg: AlertChannel, *, transition: str = "new"
    ) -> None:
        if self._notifier is None:
            return
        if transition == "new":
            delivery = self._notifier.notify(alert, cfg)
        else:
            delivery = self._notifier.notify_lifecycle(
                alert, cfg, transition=transition
            )
        if delivery.lastNotifiedAt is not None:
            self.record_alert_channel_delivery(delivery)

    def _raise_or_merge(self, *, event: AlertEvent, record: RunRecord) -> Alert | None:
        for alert in reversed(self._alerts):
            if (
                alert.rule_id == event.rule_id
                and alert.graph_id == record.graph_id
                and alert.status != "resolved"
            ):
                alert.count += 1
                alert.last_seen = record.finished_at
                alert.last_run_id = record.id
                return alert, "merged"
        self._alert_counter += 1
        alert = Alert(
            id=f"alt-{self._alert_counter}",
            rule_id=event.rule_id,
            graph_id=record.graph_id,
            severity=event.severity,
            message=event.message,
            first_seen=record.finished_at,
            last_seen=record.finished_at,
            last_run_id=record.id,
            rule_name=event.rule_name,
            assignee=self._ops.current_assignee(),
        )
        self._alerts.append(alert)
        return alert, "new"

    def raise_rollout_gate_alert(
        self,
        *,
        graph_id: str,
        message: str,
        action: dict,
        last_run_id: str = "",
    ) -> Alert:
        """M9：灰度门控越阈自动回滚后，由 routing.gate 显式产一条 rollout_gate critical 告警。

        与四内置规则不同，阈值随每图 RolloutConfig.gate（不在全局 RuleConfig），
        故不经 evaluate_rules；同 (rule_id, graph_id) 未 resolved 告警合并计数、保留首次 action。
        """
        with self._lock:
            merged_alert: Alert | None = None
            for alert in reversed(self._alerts):
                if (
                    alert.rule_id == "rollout_gate"
                    and alert.graph_id == graph_id
                    and alert.status != "resolved"
                ):
                    alert.count += 1
                    alert.last_seen = _now_iso()
                    alert.last_run_id = last_run_id or alert.last_run_id
                    merged_alert = alert
                    break
            cfg = self._channel.model_copy(deep=True)
            if merged_alert is None:
                self._alert_counter += 1
                now = _now_iso()
                alert = Alert(
                    id=f"alt-{self._alert_counter}",
                    rule_id="rollout_gate",
                    graph_id=graph_id,
                    severity="critical",
                    message=message,
                    first_seen=now,
                    last_seen=now,
                    last_run_id=last_run_id,
                    action=action,
                )
                self._alerts.append(alert)
        if merged_alert is not None:
            self._notify_outside_lock(merged_alert, cfg, transition="merged")
            return merged_alert
        self._notify_outside_lock(alert, cfg)
        return alert

    def list_runs(self, graph_id: str | None = None, limit: int = 50) -> list[RunRecord]:
        with self._lock:
            runs = list(self._runs)
        if graph_id:
            runs = [run for run in runs if run.graph_id == graph_id]
        return list(reversed(runs))[:limit]

    def get_run(self, run_id: str) -> RunRecord | None:
        """docs/33 §4：按 id 取单条运行（含 spans），供 trace 钻取端点；不存在返 None。"""
        with self._lock:
            return next((run for run in self._runs if run.id == run_id), None)

    def _apply_escalations_locked(self) -> list[Alert]:
        """docs/33 §5.2：读时惰性把超时 open warning 升级为 critical（回写进程内告警）；
        返回本次真正升级的告警，供锁外发 escalated 通知（升级幂等、只触发一次）。"""
        now = _now_iso()
        escalated: list[Alert] = []
        for alert in self._alerts:
            upgraded = apply_escalation(alert, self._rules, now)
            if upgraded is not alert:
                alert.severity = upgraded.severity
                alert.escalated_at = upgraded.escalated_at
                escalated.append(alert)
        return escalated

    def list_alerts(self, status: str | None = None) -> list[Alert]:
        with self._lock:
            escalated = self._apply_escalations_locked()
            alerts = list(self._alerts)
            cfg = self._channel.model_copy(deep=True)
        for alert in escalated:
            self._notify_outside_lock(alert, cfg, transition="escalated")
        if status:
            alerts = [alert for alert in alerts if alert.status == status]
        return list(reversed(alerts))

    def get_alert(self, alert_id: str) -> Alert | None:
        with self._lock:
            escalated = self._apply_escalations_locked()
            alert = next((item for item in self._alerts if item.id == alert_id), None)
            cfg = self._channel.model_copy(deep=True)
        for item in escalated:
            self._notify_outside_lock(item, cfg, transition="escalated")
        return alert

    def acknowledge_alert(self, alert_id: str) -> Alert | Literal[False] | None:
        with self._lock:
            alert = next((item for item in self._alerts if item.id == alert_id), None)
            if alert is None:
                return None
            if alert.status != "open":
                return False
            alert.status = "acknowledged"
            return alert

    def resolve_alert(self, alert_id: str) -> Alert | Literal[False] | None:
        with self._lock:
            alert = next((item for item in self._alerts if item.id == alert_id), None)
            if alert is None:
                return None
            if alert.status == "resolved":
                return False
            alert.status = "resolved"
            cfg = self._channel.model_copy(deep=True)
        self._notify_outside_lock(alert, cfg, transition="resolved")
        return alert

    def get_rules(self) -> RuleConfig:
        with self._lock:
            return self._rules.model_copy(deep=True)

    def update_rules(self, raw: dict) -> RuleConfig:
        errors = validate_rules(raw)
        if errors:
            raise ValueError("；".join(errors))
        rules = rules_from_raw(raw)
        with self._lock:
            self._rules = rules
            return rules.model_copy(deep=True)

    def snapshot_metrics(self) -> dict:
        from .metrics import summarize

        with self._lock:
            runs = list(self._runs)
        return summarize(runs)

    def get_alert_channel(self) -> AlertChannel:
        with self._lock:
            return self._channel.model_copy(deep=True)

    def update_alert_channel(self, raw: dict) -> AlertChannel:
        errors = validate_alert_channel(raw)
        if errors:
            raise ValueError("；".join(errors))
        channel = alert_channel_from_raw(raw)
        channel.updatedAt = _now_iso()
        with self._lock:
            self._channel = channel
            return channel.model_copy(deep=True)

    def get_alert_channel_delivery(self) -> AlertChannelDelivery:
        with self._lock:
            return self._channel_delivery.model_copy(deep=True)

    def record_alert_channel_delivery(self, delivery: AlertChannelDelivery) -> None:
        with self._lock:
            self._channel_delivery = delivery.model_copy(deep=True)

    def set_notifier(self, notifier: object | None) -> None:
        self._notifier = notifier

    # docs/33 §5：静默 / 值班（进程内，委托 OpsStore）
    def create_silence(
        self, *, rule_id: str | None, graph_id: str | None, duration_minutes: int,
        reason: str, created_by: str,
    ) -> Silence:
        return self._ops.create_silence(
            rule_id=rule_id, graph_id=graph_id, duration_minutes=duration_minutes,
            reason=reason, created_by=created_by,
        )

    def list_silences(self, active: bool | None = None) -> list[Silence]:
        return self._ops.list_silences(active)

    def delete_silence(self, silence_id: str) -> bool:
        return self._ops.delete_silence(silence_id)

    def get_oncall(self) -> OnCallSchedule:
        return self._ops.get_oncall()

    def set_oncall(self, *, members: list[str], updated_by: str) -> OnCallSchedule:
        return self._ops.set_oncall(members=members, updated_by=updated_by)

    def rotate_oncall(self, *, updated_by: str) -> OnCallSchedule:
        return self._ops.rotate_oncall(updated_by=updated_by)

    def reset(self) -> None:
        with self._lock:
            self._runs.clear()
            self._alerts.clear()
            self._streaks.clear()
            self._rules = RuleConfig()
            self._ops.reset()
            self._channel = AlertChannel()
            self._channel_delivery = AlertChannelDelivery()
