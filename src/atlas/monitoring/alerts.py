"""基础监控告警：规则模型与求值纯函数（契约 04 §5.13）。

规则在运行记录写入时由 MonitoringStore 同步调用；本模块无状态、无锁，
evaluate_rules 只产出「触发意图」，同键合并/计数由 store 负责。
"""

from typing import Literal

from pydantic import BaseModel

RuleId = Literal["run_error", "node_failed", "consecutive_failures", "failure_rate"]


class RuleToggle(BaseModel):
    enabled: bool = True


class ConsecutiveRule(BaseModel):
    enabled: bool = True
    threshold: int = 3


class FailureRateRule(BaseModel):
    enabled: bool = True
    window: int = 20
    min_samples: int = 5
    rate: float = 0.5


class RuleConfig(BaseModel):
    run_error: RuleToggle = RuleToggle()
    node_failed: RuleToggle = RuleToggle()
    consecutive_failures: ConsecutiveRule = ConsecutiveRule()
    failure_rate: FailureRateRule = FailureRateRule()


class Alert(BaseModel):
    id: str
    rule_id: RuleId
    graph_id: str
    severity: Literal["critical", "warning"]
    message: str
    first_seen: str
    last_seen: str
    count: int = 1
    status: Literal["open", "acknowledged", "resolved"] = "open"
    last_run_id: str


class AlertEvent(BaseModel):
    rule_id: RuleId
    severity: Literal["critical", "warning"]
    message: str


def _is_bool(value: object) -> bool:
    return isinstance(value, bool)


def _bounded_int(value: object) -> bool:
    return isinstance(value, int) and not _is_bool(value) and 1 <= value <= 200


def validate_rules(raw: object) -> list[str]:
    """校验 PUT 全量规则体，返回中文错误信息列表（空列表=合法）。"""
    errors: list[str] = []
    if not isinstance(raw, dict):
        return ["规则配置必须是对象"]
    for name in ("run_error", "node_failed", "consecutive_failures", "failure_rate"):
        if name not in raw:
            errors.append(f"缺少规则段：{name}")
    for name in ("run_error", "node_failed"):
        section = raw.get(name)
        if name in raw and (
            not isinstance(section, dict) or "enabled" not in section or not _is_bool(section["enabled"])
        ):
            errors.append(f"{name}.enabled 必须是布尔值")
    consecutive = raw.get("consecutive_failures")
    if isinstance(consecutive, dict):
        if "enabled" in consecutive and not _is_bool(consecutive["enabled"]):
            errors.append("consecutive_failures.enabled 必须是布尔值")
        if not _bounded_int(consecutive.get("threshold")):
            errors.append("consecutive_failures.threshold 必须是 1-200 的整数")
    elif "consecutive_failures" in raw:
        errors.append("consecutive_failures 必须是对象")
    rate_rule = raw.get("failure_rate")
    if isinstance(rate_rule, dict):
        if "enabled" in rate_rule and not _is_bool(rate_rule["enabled"]):
            errors.append("failure_rate.enabled 必须是布尔值")
        if not _bounded_int(rate_rule.get("window")):
            errors.append("failure_rate.window 必须是 1-200 的整数")
        if not _bounded_int(rate_rule.get("min_samples")):
            errors.append("failure_rate.min_samples 必须是 1-200 的整数")
        rate = rate_rule.get("rate")
        if not isinstance(rate, (int, float)) or _is_bool(rate) or not 0 <= float(rate) <= 1:
            errors.append("failure_rate.rate 必须是 0-1 之间的数值")
    elif "failure_rate" in raw:
        errors.append("failure_rate 必须是对象")
    return errors


def rules_from_raw(raw: dict) -> RuleConfig:
    return RuleConfig(
        run_error=RuleToggle(enabled=raw["run_error"]["enabled"]),
        node_failed=RuleToggle(enabled=raw["node_failed"]["enabled"]),
        consecutive_failures=ConsecutiveRule(
            enabled=raw["consecutive_failures"]["enabled"],
            threshold=raw["consecutive_failures"]["threshold"],
        ),
        failure_rate=FailureRateRule(
            enabled=raw["failure_rate"]["enabled"],
            window=raw["failure_rate"]["window"],
            min_samples=raw["failure_rate"]["min_samples"],
            rate=float(raw["failure_rate"]["rate"]),
        ),
    )


def evaluate_rules(
    *,
    record: object,
    healthy: bool,
    recent_by_graph: list,
    streak: int,
    rules: RuleConfig,
) -> list[AlertEvent]:
    """对刚写入的一次运行求值。recent_by_graph 为该图含本次在内的历史（旧→新）。"""
    events: list[AlertEvent] = []
    graph_id = record.graph_id
    run_id = record.id
    if record.status == "error" and rules.run_error.enabled:
        reason = f"：{record.error}" if record.error else ""
        events.append(
            AlertEvent(
                rule_id="run_error",
                severity="critical",
                message=f"运行异常：图 {graph_id} 第 {run_id} 次运行未捕获错误{reason}",
            )
        )
    failed_nodes = [node.node_id for node in record.nodes if node.status == "failed"]
    if failed_nodes and rules.node_failed.enabled:
        shown = "、".join(failed_nodes[:3])
        suffix = " 等" if len(failed_nodes) > 3 else ""
        events.append(
            AlertEvent(
                rule_id="node_failed",
                severity="warning",
                message=f"节点失败：图 {graph_id} 运行 {run_id} 中节点 {shown}{suffix} 失败",
            )
        )
    if not healthy and rules.consecutive_failures.enabled and streak >= rules.consecutive_failures.threshold:
        events.append(
            AlertEvent(
                rule_id="consecutive_failures",
                severity="critical",
                message=(
                    f"连续失败：图 {graph_id} 已连续 {streak} 次不健康运行"
                    f"（阈值 {rules.consecutive_failures.threshold}，运行 {run_id}）"
                ),
            )
        )
    fr = rules.failure_rate
    if not healthy and fr.enabled:
        window_runs = recent_by_graph[-fr.window :]
        if len(window_runs) >= fr.min_samples:
            unhealthy_count = sum(
                1 for run in window_runs if run.status == "error" or any(n.status == "failed" for n in run.nodes)
            )
            ratio = unhealthy_count / len(window_runs)
            if ratio >= fr.rate:
                events.append(
                    AlertEvent(
                        rule_id="failure_rate",
                        severity="critical",
                        message=(
                            f"失败率超标：图 {graph_id} 近 {len(window_runs)} 次运行"
                            f"{unhealthy_count} 次不健康（{ratio:.0%} ≥ {fr.rate:.0%}，运行 {run_id}）"
                        ),
                    )
                )
    return events
