"""docs/28 §4.2 ⑨ 自定义告警规则表达式 DSL（批 3 D28）。

覆盖：
- RuleConfig.custom 纯超集（默认 []、旧配置/PG JSONB 反序列化不 422、model_dump 承载）；
- validate_rules 对 custom 的中文 422 校验（cid/name/severity/enabled/表达式/重复 cid/非数组），
  含未知变量的表达式静态推断 unknown 放行；
- rules_from_raw 解析与缺省值；
- evaluate_rules 白名单上下文 {status,durationMs,failedCount,hasError} 命中、
  非布尔结果与求值异常 fail-safe 不告警、disabled 不触发、rule_id=custom:{cid}、rule_name/severity；
- 进程内 MonitoringStore 端到端：自定义告警落表、携带 rule_name、同 (rule_id,graph) 合并计数。
"""

import pytest

from atlas.monitoring.alerts import (
    CustomRule,
    RuleConfig,
    evaluate_rules,
    rules_from_raw,
    validate_rules,
)
from atlas.monitoring.metrics import NodeResult
from atlas.monitoring.records import MonitoringStore, RunRecord

_BASE = {
    "run_error": {"enabled": True},
    "node_failed": {"enabled": True},
    "consecutive_failures": {"enabled": True, "threshold": 3},
    "failure_rate": {"enabled": True, "window": 20, "min_samples": 5, "rate": 0.5},
}


def _record(status="completed", duration_ms=10.0, nodes=()):
    return RunRecord(
        id="run-9", graph_id="g", mode="sync", status=status,
        started_at="2026-09-20T00:00:00+00:00", finished_at="2026-09-20T00:00:01+00:00",
        duration_ms=duration_ms, nodes=list(nodes),
    )


def _rules(custom):
    rules = RuleConfig()
    object.__setattr__(rules, "custom", custom)
    return rules


# ---------- 纯超集 ----------

def test_rule_config_custom_superset_defaults_empty():
    config = RuleConfig()
    assert config.custom == []
    assert config.model_dump()["custom"] == []
    # 旧配置（无 custom 段）反序列化不报错
    assert rules_from_raw(_BASE).custom == []


# ---------- validate_rules ----------

def test_validate_accepts_well_formed_custom_rules():
    raw = dict(_BASE, custom=[
        {"cid": "c1", "name": "错误即告警",
         "expression": "{{status}} == 'error' || {{hasError}}", "severity": "critical"},
        {"cid": "c2", "name": "慢运行", "expression": "{{durationMs}} > 500"},
    ])
    assert validate_rules(raw) == []


def test_validate_custom_optional_and_may_be_omitted():
    assert validate_rules(_BASE) == []
    assert validate_rules(dict(_BASE, custom=[])) == []


def test_validate_allows_unknown_variables_as_unknown_type():
    # 含未声明变量的表达式静态推断 unknown 放行（运行期按白名单上下文求值）
    raw = dict(_BASE, custom=[
        {"cid": "c1", "name": "x", "expression": "{{notInWhitelist}} == 1"},
    ])
    assert validate_rules(raw) == []


@pytest.mark.parametrize(
    "rule, fragment",
    [
        ({"cid": "  ", "name": "n", "expression": "{{hasError}}"}, "cid 不能为空"),
        ({"cid": "x" * 65, "name": "n", "expression": "{{hasError}}"}, "cid 长度"),
        ({"cid": "c1", "name": "  ", "expression": "{{hasError}}"}, "name 不能为空"),
        ({"cid": "c1", "name": "n", "expression": "{{hasError}}", "severity": "bad"}, "severity"),
        ({"cid": "c1", "name": "n", "expression": "{{hasError}}", "enabled": "yes"}, "enabled 必须是布尔"),
        ({"cid": "c1", "name": "n", "expression": "1 +"}, "expression"),
        ({"cid": "c1", "name": "n", "expression": "1 + 2"}, "必须产出布尔"),
    ],
)
def test_validate_rejects_malformed_custom_rules(rule, fragment):
    errors = validate_rules(dict(_BASE, custom=[rule]))
    assert errors and any(fragment in e for e in errors), errors


def test_validate_rejects_duplicate_cid_and_non_list():
    dup = dict(_BASE, custom=[
        {"cid": "c1", "name": "a", "expression": "{{hasError}}"},
        {"cid": "c1", "name": "b", "expression": "{{status}} == 'error'"},
    ])
    assert any("cid 重复" in e for e in validate_rules(dup))
    assert any("custom 必须是规则数组" in e for e in validate_rules(dict(_BASE, custom="x")))


def test_rules_from_raw_parses_custom_with_defaults():
    raw = dict(_BASE, custom=[{"cid": "c1", "name": "n", "expression": "{{hasError}}"}])
    rules = rules_from_raw(raw)
    assert len(rules.custom) == 1
    rule = rules.custom[0]
    assert rule.cid == "c1" and rule.enabled is True and rule.severity == "warning"


# ---------- evaluate_rules ----------

def test_custom_rule_fires_on_error_and_has_error_context():
    rules = _rules([CustomRule(cid="c1", name="错误即告警", severity="critical",
                               expression="{{status}} == 'error' || {{hasError}}")])
    events = evaluate_rules(
        record=_record(status="error"), healthy=False, recent_by_graph=[], streak=1, rules=rules
    )
    custom = [e for e in events if e.rule_id == "custom:c1"]
    assert len(custom) == 1
    assert custom[0].severity == "critical"
    assert custom[0].rule_name == "错误即告警"


def test_custom_rule_fires_on_duration_and_failed_count():
    slow = _rules([CustomRule(cid="c2", name="慢运行", expression="{{durationMs}} > 500")])
    assert [e.rule_id for e in evaluate_rules(
        record=_record(duration_ms=1200.0), healthy=True, recent_by_graph=[], streak=0, rules=slow)] == ["custom:c2"]
    assert evaluate_rules(
        record=_record(duration_ms=10.0), healthy=True, recent_by_graph=[], streak=0, rules=slow) == []

    failed = _rules([CustomRule(cid="c3", name="有失败节点", expression="{{failedCount}} >= 1")])
    node = NodeResult(node_id="t1", node_type="tool_call", status="failed", error="e")
    hit = evaluate_rules(
        record=_record(nodes=[node]), healthy=False, recent_by_graph=[], streak=1, rules=failed)
    assert [e.rule_id for e in hit if e.rule_id.startswith("custom:")] == ["custom:c3"]
    # 无失败节点时不触发
    assert evaluate_rules(
        record=_record(), healthy=True, recent_by_graph=[], streak=0, rules=failed
    ) == []


def test_custom_rule_non_boolean_and_eval_error_fail_safe():
    # 非布尔结果（算术）不告警
    rules = _rules([CustomRule(cid="c1", name="非布尔", expression="1 + 2")])
    assert evaluate_rules(
        record=_record(), healthy=True, recent_by_graph=[], streak=0, rules=rules) == []
    # 运行期类型错误（数字与字符串有序比较）fail-safe，不抛、不告警
    rules2 = _rules([CustomRule(cid="c2", name="坏比较", expression="{{durationMs}} > 'x'")])
    assert evaluate_rules(
        record=_record(), healthy=True, recent_by_graph=[], streak=0, rules=rules2) == []


def test_disabled_custom_rule_does_not_fire():
    rules = _rules([CustomRule(cid="c1", name="关", enabled=False,
                               expression="{{status}} == 'error'")])
    events = evaluate_rules(
        record=_record(status="error"), healthy=False, recent_by_graph=[], streak=1, rules=rules
    )
    assert not any(e.rule_id == "custom:c1" for e in events)


# ---------- 进程内 store 端到端 ----------

def test_store_persists_custom_alert_with_name_and_merges():
    store = MonitoringStore()
    store.update_rules(dict(_BASE, custom=[
        {"cid": "c1", "name": "错误即告警", "severity": "critical",
         "expression": "{{status}} == 'error'"},
    ]))
    store.record_run(graph_id="g", mode="sync", status="error",
                     started_at="2026-09-20T00:00:00+00:00", duration_ms=10.0, nodes=[])
    store.record_run(graph_id="g", mode="sync", status="error",
                     started_at="2026-09-20T00:01:00+00:00", duration_ms=10.0, nodes=[])
    alerts = [a for a in store.list_alerts() if a.rule_id == "custom:c1"]
    assert len(alerts) == 1  # 同 (rule_id, graph) 合并
    alert = alerts[0]
    assert alert.count == 2
    assert alert.severity == "critical"
    assert alert.rule_name == "错误即告警"
    # 不同图不合并
    store.record_run(graph_id="other", mode="sync", status="error",
                     started_at="2026-09-20T00:02:00+00:00", duration_ms=10.0, nodes=[])
    assert len([a for a in store.list_alerts() if a.rule_id == "custom:c1"]) == 2
