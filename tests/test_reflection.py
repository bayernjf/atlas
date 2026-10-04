# -*- coding: utf-8 -*-
"""反思进化 v1 单测（打包 ZH，2026-10-01；契约 docs/88，用例 U1026–U1030）。

覆盖：白名单 fail-closed（U1026）、证据装配与 no_evidence（U1027）、
rejected_bounds/rejected_whitelist（U1028）、ok 路径与 ring/租户分区（U1029）、
摘要器确定性降级（U1030），外加 docs/88 §3 P-2(a) 的冻结断言。
"""

from __future__ import annotations

import pytest

from atlas.monitoring.business import BusinessOutcome
from atlas.monitoring.metrics import NodeResult
from atlas.monitoring.records import RunRecord
from atlas.recording.reports import ReportStore
from atlas.recording.shadow import HumanOutcome, ShadowStore, ToolIntent
from atlas.reflection import (
    TUNABLE_WHITELIST,
    Change,
    NullSummarizer,
    ReflectionStore,
    build_evidence,
    get_summarizer,
    in_bounds,
    is_whitelisted,
    run_pass,
    validate_changes,
)
from atlas.reflection.adapter import LiteLLMSummarizer, _parse_payload
from atlas.reflection.candidate import (
    ReflectionCandidate,
    ReflectionReport,
    resolve_node_ids,
    to_change,
)

TENANT = "t1"
GRAPH = "g-reflect"


# --- 测试替身 ---------------------------------------------------------------------


class _StubMonitoring:
    """只实现 build_evidence 会读的两个方法（真实 MonitoringStore 需经 record_run 落数据）。"""

    def __init__(self, runs: list[RunRecord], alerts: list | None = None) -> None:
        self._runs = runs
        self._alerts = alerts or []

    def list_runs(self, graph_id: str | None = None, limit: int = 50) -> list[RunRecord]:
        rows = [r for r in self._runs if graph_id is None or r.graph_id == graph_id]
        return rows[:limit]

    def list_alerts(self, status: str | None = None) -> list:
        return list(self._alerts)


class _StubServices:
    def __init__(self, *, runs=(), alerts=(), reports=None, shadows=None, graph_store=None) -> None:
        self.monitoring = _StubMonitoring(list(runs), list(alerts))
        self.report_store = reports if reports is not None else ReportStore()
        self.shadow_store = shadows if shadows is not None else ShadowStore()
        # 打包 ZU（docs/94 E-3）：节点定位读已发布图快照；缺省无 graph_store ⇒ 全 None。
        self.graph_store = graph_store


class _StubSummarizer:
    """确定性摘要器：固定返回预设建议，用于覆盖 ok/拒绝路径。"""

    def __init__(self, changes: list[dict], suggestions: list[str] | None = None) -> None:
        self._changes = changes
        self._suggestions = suggestions or []

    def summarize(self, evidence):
        return list(self._changes), list(self._suggestions)


class _StubAlert:
    def __init__(self, rule_id: str, count: int, status: str = "open", action=None) -> None:
        self.rule_id = rule_id
        self.graph_id = GRAPH
        self.count = count
        self.status = status
        self.action = action


def _run(
    run_id: str,
    *,
    version: int | None = 1,
    failed: tuple[str, ...] = (),
    business: BusinessOutcome | None = None,
) -> RunRecord:
    nodes = [NodeResult(node_id=node_id, node_type="tool_call", status="failed", error="boom") for node_id in failed]
    return RunRecord(
        id=run_id,
        graph_id=GRAPH,
        mode="sync",
        status="completed",
        started_at="2026-10-01T00:00:00+00:00",
        finished_at="2026-10-01T00:00:01+00:00",
        duration_ms=1000.0,
        nodes=nodes,
        resolved_version=version,
        business=business,
    )


def _evidence(**kwargs):
    services = _StubServices(**kwargs)
    return build_evidence(services, tenant_id=TENANT, graph_id=GRAPH, base_version=1)


# --- U1026 白名单与 fail-closed ----------------------------------------------------


def test_u1026_whitelist_is_exactly_four_documented_params():
    assert set(TUNABLE_WHITELIST) == {
        "approval_limit",
        "node.confidenceThreshold",
        "monitor.failure_rate.rate",
        "gate.run_error_rate",
    }
    assert TUNABLE_WHITELIST["approval_limit"].scope == "graph_variable"
    assert TUNABLE_WHITELIST["node.confidenceThreshold"].scope == "node_config"
    assert TUNABLE_WHITELIST["monitor.failure_rate.rate"].scope == "monitor_rule"
    assert TUNABLE_WHITELIST["gate.run_error_rate"].scope == "gate_config"
    # 门控阈值在 GateConfig.metrics 缺省为空列表，无代码缺省。
    assert TUNABLE_WHITELIST["gate.run_error_rate"].current is None


def test_u1026_prompt_template_is_not_whitelisted():
    """docs/88 D-4 特别条款：提示词无界，v1 只出建议文本、不进白名单。"""
    assert not is_whitelisted("promptTemplate")
    assert not is_whitelisted("node.promptTemplate")
    assert not is_whitelisted("")


def test_u1026_bounds_are_inclusive_and_reject_non_numbers():
    assert in_bounds("node.confidenceThreshold", 0.0)
    assert in_bounds("node.confidenceThreshold", 1.0)
    assert in_bounds("node.confidenceThreshold", 0.6)
    assert not in_bounds("node.confidenceThreshold", -0.01)
    assert not in_bounds("node.confidenceThreshold", 1.01)
    # bool 不是数值；字符串/None/未知键一律拒绝（fail-closed）。
    assert not in_bounds("node.confidenceThreshold", True)
    assert not in_bounds("node.confidenceThreshold", "0.6")
    assert not in_bounds("node.confidenceThreshold", None)
    assert not in_bounds("nope", 0.5)


def test_u1026_unknown_key_voids_whole_candidate():
    """整份作废：一条越权即拒，合法的那条也不落（不做部分采纳）。"""
    status, reasons = validate_changes(
        [
            Change(param_key="node.confidenceThreshold", to_value=0.7),
            Change(param_key="promptTemplate", to_value="任意文本"),
        ]
    )
    assert status == "rejected_whitelist"
    assert any("promptTemplate" in reason for reason in reasons)


# --- U1027 证据装配与 no_evidence --------------------------------------------------


def test_u1027_evidence_assembles_from_existing_projections():
    reports = ReportStore()
    reports.record(graph_id=GRAPH, trigger="manual", report={"total": 4, "passed": 3, "failed": 1})
    shadows = ShadowStore()
    saved = shadows.add(
        graph_id=GRAPH,
        trace_id="tr-1",
        decisions=[],
        tool_intents=[
            ToolIntent(node_id="n1", tool="shop/execute_refund", dry_run=True, action_status="SHADOW_DRY_RUN")
        ],
    )
    shadows.attach_outcome(saved["id"], HumanOutcome(action="refunded"))

    evidence = _evidence(
        runs=[
            _run("run-1", failed=("node-a", "node-b"), business=BusinessOutcome(auto_refunded=True)),
            _run("run-2", failed=("node-a",)),
        ],
        alerts=[_StubAlert("run_error", 3, action={"type": "rollback"})],
        reports=reports,
        shadows=shadows,
    )

    assert evidence is not None
    assert evidence.tenant_id == TENANT and evidence.graph_id == GRAPH
    assert [row.runs for row in evidence.per_version] == [2]
    # 两条 run 都带失败节点 ⇒ is_healthy 为假 ⇒ error_rate 1.0。
    assert evidence.per_version[0].error_rate == 1.0
    assert evidence.per_version[0].business is not None
    assert evidence.per_version[0].business["auto_refund_rate"] == 1.0
    assert [(row.node_id, row.count) for row in evidence.failed_nodes] == [("node-a", 2), ("node-b", 1)]
    assert evidence.replay is not None and (evidence.replay.matches, evidence.replay.total) == (3, 4)
    assert evidence.shadow is not None and evidence.shadow.match is True and evidence.shadow.total == 1
    assert [(row.rule, row.count, row.action) for row in evidence.alerts] == [("run_error", 3, "rollback")]


def test_u1027_empty_graph_yields_no_evidence_report():
    """零运行/零报告/零告警 ⇒ 证据为 None ⇒ no_evidence（不生成候选）。"""
    assert _evidence() is None

    store = ReflectionStore()
    report = run_pass(_StubServices(), store, tenant_id=TENANT, graph_id=GRAPH, base_version=1)
    assert report.status == "no_evidence"
    assert report.candidate_id is None
    assert store.list_reports() == [report.model_dump()]


def test_u1027_resolved_alerts_and_other_graphs_are_excluded():
    other = _StubAlert("node_failed", 9)
    other.graph_id = "g-other"
    resolved = _StubAlert("run_error", 5, status="resolved")
    evidence = _evidence(runs=[_run("run-1")], alerts=[other, resolved])
    assert evidence is not None
    assert evidence.alerts == []


# --- U1028 拒绝路径 ---------------------------------------------------------------


def test_u1028_out_of_bounds_rejects_candidate():
    store = ReflectionStore()
    services = _StubServices(runs=[_run("run-1")])
    report = run_pass(
        services,
        store,
        tenant_id=TENANT,
        graph_id=GRAPH,
        base_version=1,
        summarizer=_StubSummarizer([{"param_key": "node.confidenceThreshold", "to": 1.5}]),
    )
    assert report.status == "rejected_bounds"
    assert report.candidate_id is None
    assert store.get_candidate("refl-1") is None
    assert "越界" in report.reasons[0]


def test_u1028_whitelist_rejection_reports_reason():
    store = ReflectionStore()
    services = _StubServices(runs=[_run("run-1")])
    report = run_pass(
        services,
        store,
        tenant_id=TENANT,
        graph_id=GRAPH,
        base_version=1,
        summarizer=_StubSummarizer([{"param_key": "promptTemplate", "to": "换个说法"}]),
    )
    assert report.status == "rejected_whitelist"
    assert report.candidate_id is None
    assert "approval_limit" in report.reasons[0]  # 原因里给出允许列表


# --- U1029 ok 路径、ring 与租户分区 -----------------------------------------------


def test_u1029_ok_path_stores_candidate_and_report():
    store = ReflectionStore()
    services = _StubServices(runs=[_run("run-1", failed=("node-a",))])
    report = run_pass(
        services,
        store,
        tenant_id=TENANT,
        graph_id=GRAPH,
        base_version=2,
        summarizer=_StubSummarizer(
            [{"param_key": "approval_limit", "to": 300, "reason": "超限额转人工偏多"}],
            ["在 prompt 里补一句：金额超限必须转人工"],
        ),
        now="2026-10-01T00:00:00+00:00",
    )

    assert report.status == "ok"
    assert report.candidate_id == "refl-1"
    candidate = store.get_candidate("refl-1")
    assert candidate is not None
    assert candidate["candidate_id"] == "refl-1"
    assert candidate["base_version"] == 2
    # 对外用别名 from/to（docs/88 §4、docs/12 端点契约）；打包 ZU 起每条 change
    # 显式带 node_id（无节点定位时为 null，docs/94 §3.2／U1118）。
    assert candidate["changes"] == [
        {
            "param_key": "approval_limit",
            "from": "500",
            "to": 300,
            "reason": "超限额转人工偏多",
            "node_id": None,
        }
    ]
    assert candidate["prompt_suggestions"] == ["在 prompt 里补一句：金额超限必须转人工"]
    assert candidate["evidence_digest"].startswith(f"{GRAPH}@2")
    assert candidate["generated_at"] == "2026-10-01T00:00:00+00:00"
    # 打包 ZU：候选级处理标记缺省为待处理（null）。
    assert candidate["decision_status"] is None
    assert candidate["decided_at"] is None


def test_u1029_candidate_ids_increment_and_ring_is_bounded():
    store = ReflectionStore(report_maxlen=2, candidate_maxlen=2)
    services = _StubServices(runs=[_run("run-1")])
    summarizer = _StubSummarizer([{"param_key": "node.confidenceThreshold", "to": 0.7}])
    for _ in range(3):
        run_pass(services, store, tenant_id=TENANT, graph_id=GRAPH, base_version=1, summarizer=summarizer)

    reports = store.list_reports()
    assert [row["candidate_id"] for row in reports] == ["refl-3", "refl-2"]  # 新→旧，ring 截断
    assert store.get_candidate("refl-1") is None
    assert store.get_candidate("refl-3") is not None


def test_u1029_stores_are_per_tenant_and_reset_clears():
    a, b = ReflectionStore(), ReflectionStore()
    services = _StubServices(runs=[_run("run-1")])
    summarizer = _StubSummarizer([{"param_key": "node.confidenceThreshold", "to": 0.7}])
    run_pass(services, a, tenant_id="t1", graph_id=GRAPH, base_version=1, summarizer=summarizer)

    assert a.list_reports() and b.list_reports() == []
    assert b.get_candidate("refl-1") is None
    a.reset()
    assert a.list_reports() == [] and a.get_candidate("refl-1") is None
    assert a.next_candidate_id() == "refl-1"  # 计数归零


# --- U1030 摘要器确定性降级 --------------------------------------------------------


def test_u1030_null_summarizer_yields_ok_without_candidate(monkeypatch):
    monkeypatch.delenv("LITELLM_MODEL", raising=False)
    assert isinstance(get_summarizer(), NullSummarizer)

    store = ReflectionStore()
    report = run_pass(_StubServices(runs=[_run("run-1")]), store, tenant_id=TENANT, graph_id=GRAPH, base_version=1)
    assert report.status == "ok"
    assert report.candidate_id is None
    assert store.list_reports()[0]["status"] == "ok"


def test_u1030_litellm_summarizer_selected_by_env(monkeypatch):
    monkeypatch.setenv("LITELLM_MODEL", "openai/gpt-4o-mini")
    assert isinstance(get_summarizer(), LiteLLMSummarizer)


def test_u1030_payload_parsing_drops_bad_rows():
    """坏行逐条丢弃；非字符串提示词丢弃；`from` 不在这一层补（由 to_change 统一补）。"""
    changes, suggestions = _parse_payload(
        {
            "changes": [
                {"param_key": "approval_limit", "to": 800},
                {"param_key": "gate.run_error_rate", "to": 0.2},
                {"to": 1},  # 缺 param_key
                "不是对象",
            ],
            "prompt_suggestions": ["有效建议", "", 42],
        }
    )
    assert changes == [
        {"param_key": "approval_limit", "to": 800, "reason": ""},
        {"param_key": "gate.run_error_rate", "to": 0.2, "reason": ""},
    ]
    assert suggestions == ["有效建议"]


def test_u1030_to_change_fills_from_whitelist_current():
    assert to_change({"param_key": "approval_limit", "to": 800}) == Change(
        param_key="approval_limit", from_value="500", to_value=800
    )
    # 门控阈值无代码缺省 → from 为 None；越权键同样留 None（由 validate_changes 拒绝）。
    assert to_change({"param_key": "gate.run_error_rate", "to": 0.2}).from_value is None
    assert to_change({"param_key": "promptTemplate", "to": "x"}).from_value is None
    # 显式给了 from 就不覆盖。
    assert to_change({"param_key": "approval_limit", "from": 100, "to": 200}).from_value == 100


def test_u1030_payload_must_be_object():
    with pytest.raises(ValueError):
        _parse_payload(["not", "a", "dict"])


# --- docs/88 §3 P-2(a) 冻结断言 ----------------------------------------------------


def test_p2a_placeholder_stays_placeholder():
    """`engine/loop.py` 与 `reflect_node` 冻结为历史骨架：占位行为不得被"实现"。"""
    from atlas.engine.loop import run_loop
    from atlas.engine.nodes import reflect_node

    state = run_loop("冻结断言", max_steps=1)
    assert state["status"] == "completed"

    out = reflect_node({"variables": {"step": 2}})
    assert out == {"current_node": "reflect", "messages": ["reflect@2: loop finished"]}


# --- 打包 ZU（docs/94）U1115–U1119：采纳状态持久化＋节点级定位 -----------------------


def _seed_candidate(
    store: ReflectionStore,
    cid: str = "refl-1",
    *,
    param_key: str = "approval_limit",
    to_value: int | float | str = 300,
    node_id: str | None = None,
) -> ReflectionCandidate:
    candidate = ReflectionCandidate(
        candidate_id=cid,
        graph_id=GRAPH,
        base_version=1,
        changes=[Change(
            param_key=param_key, from_value=0.6, to_value=to_value, node_id=node_id,
        )],
        prompt_suggestions=["建议"],
        evidence_digest=f"{GRAPH}@1",
        generated_at="2026-10-01T00:00:00+00:00",
    )
    return store.add_candidate(candidate)


def test_u1115_inmemory_record_decision_lifecycle():
    store = ReflectionStore()
    _seed_candidate(store)

    cand = store.get_candidate("refl-1")
    assert cand["decision_status"] is None
    assert cand["decided_at"] is None

    # 登记 adopted。
    assert store.record_decision("refl-1", "adopted", decided_at="2026-10-02T00:00:00+00:00") is True
    cand = store.get_candidate("refl-1")
    assert cand["decision_status"] == "adopted"
    assert cand["decided_at"] == "2026-10-02T00:00:00+00:00"

    # 允许改判覆盖并刷新 decided_at。
    assert store.record_decision("refl-1", "dismissed", decided_at="2026-10-03T00:00:00+00:00") is True
    cand = store.get_candidate("refl-1")
    assert cand["decision_status"] == "dismissed"
    assert cand["decided_at"] == "2026-10-03T00:00:00+00:00"

    # 不存在返 False（API 层据此 404）。
    assert store.record_decision("refl-999", "adopted") is False

    # reset 清空候选与决策。
    store.reset()
    assert store.get_candidate("refl-1") is None


def test_u1116_list_reports_carries_live_decision_status():
    store = ReflectionStore()
    # 一条无候选报告（no_evidence）：decision_status 键存在且为 None。
    store.add_report(ReflectionReport(
        graph_id=GRAPH, base_version=1, status="no_evidence",
        reasons=["无证据"], generated_at="2026-10-01T00:00:00+00:00",
    ))
    # 一条带候选的 ok 报告。
    services = _StubServices(runs=[_run("run-1", failed=("node-a",))])
    summarizer = _StubSummarizer([{"param_key": "approval_limit", "to": 300}])
    run_pass(
        services, store, tenant_id=TENANT, graph_id=GRAPH, base_version=1,
        summarizer=summarizer, now="2026-10-01T00:01:00+00:00",
    )

    reports = store.list_reports()
    by_status = {row["status"]: row for row in reports}
    assert "decision_status" in by_status["no_evidence"]
    assert by_status["no_evidence"]["decision_status"] is None
    ok_row = by_status["ok"]
    assert ok_row["candidate_id"] == "refl-1"
    assert ok_row["decision_status"] is None  # 初始待处理

    # 登记后列表动态反映（不依赖落报告时的快照）。
    store.record_decision("refl-1", "adopted", decided_at="2026-10-02T00:00:00+00:00")
    ok_now = next(row for row in store.list_reports() if row["status"] == "ok")
    assert ok_now["decision_status"] == "adopted"


def _graph_snapshot(node_specs: list[tuple[str, str]]) -> dict:
    return {
        "version": 1,
        "variables": [],
        "nodes": [
            {"id": nid, "type": ntype, "name": nid} for nid, ntype in node_specs
        ],
        "edges": [],
    }


def test_u1117_resolve_node_ids_rules():
    threshold = lambda nid=None: Change(  # noqa: E731
        param_key="node.confidenceThreshold", from_value=0.6, to_value=0.7, node_id=nid
    )

    # ① 恰好 1 个 ai_decision：无 node_id 自动填。
    changes = [threshold()]
    resolve_node_ids(changes, _graph_snapshot([("ai-1", "ai_decision"), ("tool-1", "tool_call")]))
    assert changes[0].node_id == "ai-1"

    # ② ≥2 个 ai_decision：无法确定，保持 None（不猜）。
    changes = [threshold()]
    resolve_node_ids(changes, _graph_snapshot([("ai-1", "ai_decision"), ("ai-2", "ai_decision")]))
    assert changes[0].node_id is None

    # ③ 0 个 ai_decision：None。
    changes = [threshold()]
    resolve_node_ids(changes, _graph_snapshot([("tool-1", "tool_call")]))
    assert changes[0].node_id is None

    # ④ LLM 给的 node_id 指向存在的 ai_decision：保留；指向不存在/非 ai_decision：丢弃后走补全。
    keep = [threshold("ai-1")]
    resolve_node_ids(keep, _graph_snapshot([("ai-1", "ai_decision")]))
    assert keep[0].node_id == "ai-1"

    drop_wrong_type = [threshold("tool-1")]  # 指向非 ai_decision，但图里恰有 1 个 ai_decision
    resolve_node_ids(
        drop_wrong_type,
        _graph_snapshot([("tool-1", "tool_call"), ("ai-9", "ai_decision")]),
    )
    assert drop_wrong_type[0].node_id == "ai-9"  # 丢弃错误 id 后自动补唯一 ai_decision

    drop_missing = [threshold("ghost")]  # 指向不存在节点，图里有 2 个 ai_decision ⇒ 补不了
    resolve_node_ids(drop_missing, _graph_snapshot([("ai-1", "ai_decision"), ("ai-2", "ai_decision")]))
    assert drop_missing[0].node_id is None

    # ⑤ 其余三条 key 的 change node_id 恒为 None（即使误带也清空）。
    others = [
        Change(param_key=key, from_value=None, to_value=1, node_id="ai-1")
        for key in ("approval_limit", "monitor.failure_rate.rate", "gate.run_error_rate")
    ]
    resolve_node_ids(others, _graph_snapshot([("ai-1", "ai_decision")]))
    assert all(c.node_id is None for c in others)

    # ⑥ 快照缺失/形状异常：全 None 且不抛。
    none_changes = [threshold("ai-1")]
    resolve_node_ids(none_changes, None)
    assert none_changes[0].node_id is None
    bad_changes = [threshold()]
    resolve_node_ids(bad_changes, {"nodes": "not-a-list"})
    assert bad_changes[0].node_id is None


def test_u1117_run_pass_fills_node_id_from_snapshot():
    """端到端：pass 时恰好 1 个 ai_decision，落库候选的 change 带 node_id。"""
    snapshot = _graph_snapshot([("ai-decide", "ai_decision"), ("tool-1", "tool_call")])

    class _GraphStore:
        def get(self, graph_id, release_version=None):
            return snapshot if graph_id == GRAPH and release_version == 1 else None

    services = _StubServices(
        runs=[_run("run-1", failed=("ai-decide",))], graph_store=_GraphStore()
    )
    summarizer = _StubSummarizer([{"param_key": "node.confidenceThreshold", "to": 0.7}])
    store = ReflectionStore()
    run_pass(
        services, store, tenant_id=TENANT, graph_id=GRAPH, base_version=1,
        summarizer=summarizer, now="2026-10-01T00:00:00+00:00",
    )
    candidate = store.get_candidate("refl-1")
    assert candidate["changes"][0]["node_id"] == "ai-decide"


def test_u1118_change_node_id_default_and_legacy_compat():
    # 缺省序列化显式含 node_id=null（docs/94 §3.2／U1118）。
    fresh = Change(param_key="node.confidenceThreshold", from_value=0.6, to_value=0.7)
    assert fresh.model_dump(by_alias=True)["node_id"] is None

    # 旧 JSONB（无 node_id 键）model_validate 不报错，node_id 缺省 None。
    legacy = Change.model_validate(
        {"param_key": "node.confidenceThreshold", "from": 0.6, "to": 0.7, "reason": ""}
    )
    assert legacy.node_id is None

    # fail-closed 白名单/越界校验不因 node_id 改变：带 node_id 的越界值仍 rejected_bounds。
    status, _ = validate_changes(
        [Change(param_key="node.confidenceThreshold", to_value=9.9, node_id="ai-1")]
    )
    assert status == "rejected_bounds"


def test_u1118_payload_parser_passes_through_node_id():
    changes, _ = _parse_payload(
        {"changes": [
            {"param_key": "node.confidenceThreshold", "to": 0.7, "node_id": "ai-1"},
            {"param_key": "approval_limit", "to": 300},  # 无 node_id ⇒ 不带该键
            {"param_key": "gate.run_error_rate", "to": 0.2, "node_id": "  "},  # 空白丢弃
        ]}
    )
    assert changes[0]["node_id"] == "ai-1"
    assert "node_id" not in changes[1]
    assert "node_id" not in changes[2]


# --- U1119 写端点（权限/404/422/审计/无副作用）---------------------------------------

def _api_client():
    from fastapi.testclient import TestClient

    from atlas.api.main import app

    return TestClient(app)


def _t1_services():
    from atlas.api.main import tenant_registry

    return tenant_registry.get("t1")


def _login(client, username: str, password: str) -> dict[str, str]:
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['token']}"}


def test_u1119_put_decision_operator_succeeds_and_returns_projection(monkeypatch):
    client = _api_client()
    services = _t1_services()
    services.reflection_store.reset()
    cid = services.reflection_store.next_candidate_id()
    _seed_candidate(services.reflection_store, cid)

    # T22 不变量：决策端点绝不能触达发布/放量。给 publish 装一个一调就失败的哨子。
    def _forbid_publish(*args, **kwargs):
        raise AssertionError("decision endpoint must not publish")

    monkeypatch.setattr(services.graph_store, "publish", _forbid_publish)

    headers = _login(client, "operator-a", "operator123")  # operate 角色
    resp = client.put(
        f"/api/reflection/candidates/{cid}/decision",
        json={"status": "adopted"}, headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["candidate_id"] == cid
    assert body["decision_status"] == "adopted"
    assert body["decided_at"]  # 非空时间戳

    # 审计落一条 reflection.decide.adopted。
    events = services.audit_store.list(action_prefix="reflection.decide")
    assert any(e["action"] == "reflection.decide.adopted" for e in events)
    services.reflection_store.reset()


def test_u1119_put_decision_viewer_forbidden():
    client = _api_client()
    services = _t1_services()
    services.reflection_store.reset()
    cid = services.reflection_store.next_candidate_id()
    _seed_candidate(services.reflection_store, cid)

    headers = _login(client, "viewer-a", "viewer123")  # 仅 read
    resp = client.put(
        f"/api/reflection/candidates/{cid}/decision",
        json={"status": "adopted"}, headers=headers,
    )
    assert resp.status_code == 403
    # 被拒后状态不变。
    assert services.reflection_store.get_candidate(cid)["decision_status"] is None
    services.reflection_store.reset()


def test_u1119_put_decision_unknown_and_cross_tenant_404():
    client = _api_client()
    admin_t1 = _login(client, "admin-a", "admin123")
    resp = client.put(
        "/api/reflection/candidates/refl-does-not-exist/decision",
        json={"status": "adopted"}, headers=admin_t1,
    )
    assert resp.status_code == 404

    # 跨租户：候选在 t1，t2 管理员访问统一 404（不泄漏存在性）。
    services_t1 = _t1_services()
    services_t1.reflection_store.reset()
    cid = services_t1.reflection_store.next_candidate_id()
    _seed_candidate(services_t1.reflection_store, cid)
    admin_t2 = _login(client, "admin-b", "admin123")
    resp = client.put(
        f"/api/reflection/candidates/{cid}/decision",
        json={"status": "adopted"}, headers=admin_t2,
    )
    assert resp.status_code == 404
    services_t1.reflection_store.reset()


def test_u1119_put_decision_invalid_status_422():
    client = _api_client()
    services = _t1_services()
    services.reflection_store.reset()
    cid = services.reflection_store.next_candidate_id()
    _seed_candidate(services.reflection_store, cid)

    headers = _login(client, "admin-a", "admin123")
    resp = client.put(
        f"/api/reflection/candidates/{cid}/decision",
        json={"status": "maybe"}, headers=headers,
    )
    assert resp.status_code == 422
    services.reflection_store.reset()
