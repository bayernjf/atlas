"""灰度指标门控：canary 期越阈自动回滚（M9，金融硬条款；契约 03 `business_metrics`、04 §5.16、06 §6.11/§6.17）。

``evaluate_after_run`` 由 API 层在**两个真实运行入口的 record_run 之后**调用
（sync /run 与非 debug /run/stream）；replay/debug/subgraph 重入不经过这两个入口、
天然不触发。执行器（graph/loader）零分支，routing→monitoring 单向只读，
monitoring 不反向依赖 routing（06 §6.11）。

规则（12 §3.12）：
- 仅当 record.resolved_version == 该图 candidate 且 rollout status=canary
  且 gate.autoRollback=true 时求值；
- 按 finished_at 取 observeMinutes 窗内该图 candidate 运行；样本 < 指标 minSamples 不判；
- run_error_rate 复用 monitoring is_healthy（分母＝窗内全部 candidate 运行）；
  manual_escalation_rate / refund_amount_diff_rate 取 business 段（分母＝有业务结果运行）；
- 任一越阈（严格 >）→ routing_store.rollback(actor="auto") + rollout_gate critical
  告警（Alert.action 携带 from/to/reason/actor）；
- **只自动回滚、不自动 promote**；回滚只切新流量，不改写外部已发生事实（已退款不可逆）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from ..monitoring.metrics import is_healthy

# 业务结果类指标 id（分母＝有业务结果的 run）；run_error_rate 分母＝全部 candidate run
_BUSINESS_METRICS = ("manual_escalation_rate", "refund_amount_diff_rate")


def _within_window(finished_at: str, observe_minutes: int, *, now: datetime | None = None) -> bool:
    try:
        finished = datetime.fromisoformat(finished_at)
    except ValueError:
        return False
    reference = now or datetime.now(timezone.utc)
    if finished.tzinfo is None:
        finished = finished.replace(tzinfo=timezone.utc)
    return reference - finished <= timedelta(minutes=observe_minutes)


def _metric_min_samples(metric: Any, gate: Any) -> int:
    return metric.minSamples if metric.minSamples is not None else gate.minSamples


def _business_rates(runs: list[Any]) -> tuple[float | None, float | None, int]:
    """返 (manual_escalation_rate, refund_amount_diff_rate, samples)；样本 0 率为 None。"""
    business_runs = [run for run in runs if getattr(run, "business", None) is not None]
    samples = len(business_runs)
    if samples == 0:
        return None, None, 0
    manual = sum(1 for run in business_runs if run.business.manual_escalated) / samples
    diff = sum(1 for run in business_runs if run.business.amount_diff) / samples
    return manual, diff, samples


def _stable_reference(runs: list[Any], stable_version: int | None) -> str:
    """compareWith/stable 版本同期业务对照值（仅告警 message 附注，不阻断；样本不足明示）。"""
    if stable_version is None:
        return "stable 对照：无 stable 版本"
    manual, diff, samples = _business_rates(
        [run for run in runs if run.resolved_version == stable_version]
    )
    if samples == 0:
        return f"stable(v{stable_version}) 对照：样本不足（0）"
    return (
        f"stable(v{stable_version}) 同期：人工升级率 {manual:.1%}、金额差异率 {diff:.1%}"
        f"（样本 {samples}）"
    )


def evaluate_after_run(services: Any, record: Any) -> None:
    """单次真实运行落库后求值；满足回滚条件则切流并产告警，否则静默返回。"""
    if record.resolved_version is None:
        return
    snapshot = services.routing_store.snapshot(record.graph_id)
    if snapshot.status != "canary" or snapshot.candidate != record.resolved_version:
        return
    config = snapshot.config
    if config is None or not config.gate.autoRollback:
        return
    gate = config.gate
    if not gate.metrics:
        return

    recent = services.monitoring.list_runs(record.graph_id, limit=200)
    window = [
        run
        for run in recent
        if run.resolved_version == snapshot.candidate
        and _within_window(run.finished_at, gate.observeMinutes)
    ]
    business_window = [run for run in window if getattr(run, "business", None) is not None]
    manual_rate, diff_rate, business_samples = _business_rates(window)

    breaches: list[str] = []
    for metric in gate.metrics:
        min_samples = _metric_min_samples(metric, gate)
        if metric.id == "run_error_rate":
            if len(window) < min_samples:
                continue
            unhealthy = sum(1 for run in window if not is_healthy(run))
            rate = unhealthy / len(window)
            samples = len(window)
        elif metric.id == "manual_escalation_rate":
            if business_samples < min_samples:
                continue
            rate, samples = manual_rate, business_samples
        elif metric.id == "refund_amount_diff_rate":
            if business_samples < min_samples:
                continue
            rate, samples = diff_rate, business_samples
        else:  # pragma: no cover - Literal 已穷尽
            continue
        if rate is not None and rate > metric.threshold:
            breaches.append(
                f"{metric.id} 实测 {rate:.1%} > 阈值 {metric.threshold:.0%}"
                f"（观察窗 {gate.observeMinutes} 分钟，样本 {samples}）"
            )

    if not breaches:
        return

    reason = "灰度门控越阈自动回滚：" + "；".join(breaches)
    action = {
        "type": "rollback",
        "from_version": snapshot.candidate,
        "to_version": snapshot.stable,
        "reason": reason,
        "actor": "auto",
    }
    try:
        services.routing_store.rollback(record.graph_id, actor="auto", reason=reason)
    except Exception:
        # 已被手动/并发回滚（幂等）或状态已迁移：不重复产告警
        return
    message = reason + "。" + _stable_reference(recent, snapshot.stable)
    services.monitoring.raise_rollout_gate_alert(
        graph_id=record.graph_id,
        message=message,
        action=action,
        last_run_id=record.id,
    )
