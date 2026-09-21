"""基础监控告警：规则模型与求值纯函数（契约 04 §5.13）。

规则在运行记录写入时由 MonitoringStore 同步调用；本模块无状态、无锁，
evaluate_rules 只产出「触发意图」，同键合并/计数由 store 负责。
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from atlas.graph.conditions import ConditionEvalError, evaluate_expression, validate_expression

# rule_id 放宽为 str：内置四条 + rollout_gate 用字面量，自定义规则为 "custom:{cid}"
# （docs/28 §4.2 ⑨；PG monitoring_alerts.rule_id 本就是 TEXT）。
RuleId = str


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


class CustomRule(BaseModel):
    """docs/28 §4.2 ⑨ 自定义告警规则（表达式复用 graph.conditions 安全引擎，禁 eval）。"""

    cid: str  # 客户端 crypto.randomUUID()，后端只校验非空与长度 ≤64、同配置唯一
    name: str
    enabled: bool = True
    expression: str
    severity: Literal["critical", "warning"] = "warning"


class RuleConfig(BaseModel):
    run_error: RuleToggle = RuleToggle()
    node_failed: RuleToggle = RuleToggle()
    consecutive_failures: ConsecutiveRule = ConsecutiveRule()
    failure_rate: FailureRateRule = FailureRateRule()
    custom: list[CustomRule] = Field(default_factory=list)
    # docs/33 §5.2：warning 未确认 N 分钟后惰性升级 critical；None/缺省关闭（1-10080）
    escalation_ack_minutes: int | None = None


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
    # M9 纯超集：仅 rollout_gate 告警携带自动回滚动作 {type,from_version,to_version,reason,actor}
    action: dict | None = None
    # docs/28 §4.2 ⑨：自定义规则名（内置规则缺省 None；PG 档 v1 不持久化此字段，读回为 None）
    rule_name: str | None = None
    # docs/33 §5.2：惰性升级时间（v1 进程内，PG 读回 None 后重新惰性评估，语义幂等）
    escalated_at: str | None = None
    # docs/33 §5.3：新建时值班人（v1 进程内，PG alerts 不加列、读回 null）
    assignee: str | None = None


class AlertEvent(BaseModel):
    rule_id: RuleId
    severity: Literal["critical", "warning"]
    message: str
    rule_name: str | None = None


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
    # docs/33 §5.2：未确认升级分钟数（可选；null/缺省关闭，1-10080）
    if "escalation_ack_minutes" in raw:
        value = raw["escalation_ack_minutes"]
        if value is not None and (
            not isinstance(value, int) or _is_bool(value) or not 1 <= value <= 10080
        ):
            errors.append("escalation_ack_minutes 必须是 1-10080 的整数或 null")
    # docs/28 §4.2 ⑨：自定义规则段可选（缺省/空合法，旧配置与 PG JSONB 反序列化不 422）
    if "custom" in raw:
        custom_raw = raw["custom"]
        if not isinstance(custom_raw, list):
            errors.append("custom 必须是规则数组")
        else:
            seen_cids: set[str] = set()
            for index, rule in enumerate(custom_raw):
                prefix = f"custom[{index}]"
                if not isinstance(rule, dict):
                    errors.append(f"{prefix} 必须是对象")
                    continue
                cid = str(rule.get("cid", "")).strip()
                if not cid:
                    errors.append(f"{prefix}.cid 不能为空")
                elif len(cid) > 64:
                    errors.append(f"{prefix}.cid 长度不能超过 64")
                elif cid in seen_cids:
                    errors.append(f"{prefix}.cid 重复：{cid}")
                else:
                    seen_cids.add(cid)
                name = str(rule.get("name", "")).strip()
                if not name:
                    errors.append(f"{prefix}.name 不能为空")
                elif len(name) > 50:
                    errors.append(f"{prefix}.name 长度不能超过 50")
                if "enabled" in rule and not _is_bool(rule["enabled"]):
                    errors.append(f"{prefix}.enabled 必须是布尔值")
                if rule.get("severity", "warning") not in ("critical", "warning"):
                    errors.append(f"{prefix}.severity 必须是 critical 或 warning")
                expression = rule.get("expression", "")
                if not isinstance(expression, str):
                    errors.append(f"{prefix}.expression 必须是字符串")
                else:
                    for expr_error in validate_expression(expression):
                        errors.append(f"{prefix}.expression：{expr_error}")
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
        custom=[
            CustomRule(
                cid=str(rule.get("cid", "")).strip(),
                name=str(rule.get("name", "")).strip(),
                enabled=rule.get("enabled", True),
                expression=rule["expression"],
                severity=rule.get("severity", "warning"),
            )
            for rule in raw.get("custom", [])
            if isinstance(rule, dict)
        ],
        escalation_ack_minutes=raw.get("escalation_ack_minutes"),
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
    events.extend(_evaluate_custom_rules(rules=rules, record=record, graph_id=graph_id, run_id=run_id))
    return events


def _evaluate_custom_rules(*, rules: RuleConfig, record: object, graph_id: str, run_id: str) -> list[AlertEvent]:
    """docs/28 §4.2 ⑨：以扁平白名单上下文求自定义规则；非布尔/任何异常 fail-safe 不告警。"""
    failed_nodes = [node for node in record.nodes if node.status == "failed"]
    context = {
        "status": record.status,  # completed | error | cancelled
        # durationMs 取整毫秒：条件引擎有序比较要求两侧严格同型（int 字面量 vs int），
        # 传 float 会令 `{{durationMs}} > 500` 静态/运行期判类型不符而 fail-safe（docs/28 §4.2）。
        "durationMs": int(round(record.duration_ms)),
        "failedCount": len(failed_nodes),  # int
        "hasError": record.status == "error" or bool(failed_nodes),  # bool
    }
    events: list[AlertEvent] = []
    for rule in rules.custom:
        if not rule.enabled:
            continue
        try:
            matched = evaluate_expression(rule.expression, context)
        except Exception:  # ConditionEvalError 或任何异常一律 fail-safe，不告警、不阻塞运行
            continue
        if matched is not True:
            continue
        events.append(
            AlertEvent(
                rule_id=f"custom:{rule.cid}",
                severity=rule.severity,
                message=f"自定义规则「{rule.name}」触发：图 {graph_id} 运行 {run_id}",
                rule_name=rule.name,
            )
        )
    return events


def apply_escalation(alert: Alert, rules: RuleConfig, now: str) -> Alert:
    """docs/33 §5.2：惰性未确认升级。仅 open + warning + 配置非空 + 距 first_seen（缺省 last_seen）
    达 N 分钟且未升级过时，返 severity=critical/escalated_at=now 的副本；其余原样返回。

    已 acknowledged/resolved 不升级；已升级幂等不重复。时间字符串均为 tz-aware ISO。
    """
    minutes = rules.escalation_ack_minutes
    if alert.status != "open" or alert.severity != "warning" or not minutes:
        return alert
    if alert.escalated_at is not None:
        return alert
    base = alert.first_seen or alert.last_seen
    try:
        elapsed = (datetime.fromisoformat(now) - datetime.fromisoformat(base)).total_seconds()
    except ValueError:
        return alert
    if elapsed >= minutes * 60:
        return alert.model_copy(update={"severity": "critical", "escalated_at": now})
    return alert
