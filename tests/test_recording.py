"""U28：录制用例存储 / 归一化 / 审批预置 / 回放比对纯逻辑（04 §5.11，06 §6.9）。"""

import pytest

from atlas.recording import (
    RecordStep,
    RecordingCase,
    RecordingCreateRequest,
    RecordingStore,
    build_tool_mocks,
    collect_steps,
    compare,
    dedupe_steps,
    normalize,
    preset_approvals,
)


def _human_step(node_id: str, decision: str, token: str = "tok-1") -> RecordStep:
    return RecordStep(
        node_id=node_id,
        node_type="human_approval",
        output={
            "mode": "human_approval",
            "decision": decision,
            "target": "tool-x",
            "token": token,
            "summary": "s",
            "approver": "客服组长",
            "resolvedBy": "human",
        },
    )


def _message_step(node_id: str, message_id: str, sent_at: str) -> RecordStep:
    return RecordStep(
        node_id=node_id,
        node_type="tool_call",
        output={
            "result": {
                "id": message_id,
                "channel": "email",
                "to": ["a@example.com"],
                "subject": "退款通知",
                "body": "order_id=12345 已退款",
                "sent_at": sent_at,
            },
            "action_status": "SUCCESS",
        },
    )


def _http_step(node_id: str, date_value: str, order_id: str = "12345") -> RecordStep:
    return RecordStep(
        node_id=node_id,
        node_type="tool_call",
        output={
            "result": {
                "status": 200,
                "headers": {"content-type": "application/json", "date": date_value},
                "body": {"order_id": order_id, "status": "refunded"},
            },
            "action_status": "SUCCESS",
        },
    )


# ---------- normalize ----------

def test_normalize_strips_approval_token_but_keeps_decision():
    out = normalize(_human_step("human-1", "approved").output)
    assert "token" not in out
    assert out["decision"] == "approved"
    assert out["resolvedBy"] == "human"


def test_normalize_strips_message_sent_at_and_uuid_id_but_keeps_business_fields():
    out = normalize(_message_step("tool-msg", "msg-uuid-1", "2026-09-15T01:00:00+00:00").output)
    record = out["result"]
    assert "sent_at" not in record
    assert "id" not in record
    assert record["channel"] == "email"
    assert "order_id=12345" in record["body"]
    assert out["action_status"] == "SUCCESS"


def test_normalize_does_not_drop_plain_ids_without_sent_at():
    out = normalize({"id": "business-order-1", "order_id": "12345", "nested": {"id": "line-1"}})
    assert out["id"] == "business-order-1"
    assert out["nested"]["id"] == "line-1"


def test_normalize_strips_http_date_header_only_for_http_tool():
    out = normalize(_http_step("tool-http", "Mon, 15 Sep 2026 01:00:00 GMT").output, tool="http/request")
    headers = out["result"]["headers"]
    assert "date" not in headers
    assert headers["content-type"] == "application/json"
    assert out["result"]["body"]["order_id"] == "12345"


def test_normalize_without_http_tool_keeps_date_header():
    raw = _http_step("tool-http", "Mon, 15 Sep 2026 01:00:00 GMT").output
    assert normalize(raw)["result"]["headers"]["date"]


def test_normalize_recurses_lists_and_does_not_mutate_source():
    raw = {"items": [{"token": "t", "order_id": "1"}], "token": "root-tok"}
    out = normalize(raw)
    assert out == {"items": [{"order_id": "1"}]}
    assert raw["token"] == "root-tok"
    assert raw["items"][0]["token"] == "t"


def test_normalize_strips_span_metadata_keys_at_any_depth():
    # M10 U52：span 三元组与 graphVersion 是运行时元数据，逐节点比对前剔除（业务键保留）
    raw = {
        "traceId": "a" * 32,
        "spanId": "b" * 16,
        "parentSpanId": "c" * 16,
        "graphVersion": "g@3",
        "order_id": "12345",
        "nested": {
            "traceId": "d" * 32,
            "kept": 1,
            "items": [{"spanId": "e" * 16, "x": 2}],
        },
    }
    assert normalize(raw) == {
        "order_id": "12345",
        "nested": {"kept": 1, "items": [{"x": 2}]},
    }


def test_message_records_with_different_volatile_values_normalize_equal():
    a = normalize(_message_step("m", "uuid-a", "2026-09-15T01:00:00+00:00").output)
    b = normalize(_message_step("m", "uuid-b", "2026-09-15T02:00:00+00:00").output)
    assert a == b


def test_http_steps_with_different_dates_normalize_equal():
    a = normalize(_http_step("h", "Mon, 15 Sep 2026 01:00:00 GMT").output, tool="http/request")
    b = normalize(_http_step("h", "Mon, 15 Sep 2026 09:00:00 GMT").output, tool="http/request")
    assert a == b


def test_normalize_trigger_strips_injected_approvals_preset_but_keeps_payload():
    raw = {
        "context": {
            "triggerType": "webhook",
            "cron": "",
            "webhookUrl": "/hooks/x",
            "payload": {
                "order_id": "12345",
                "approvals": {"approval-1": "rejected"},
            },
        }
    }
    out = normalize(raw, node_type="trigger")
    payload = out["context"]["payload"]
    assert "approvals" not in payload
    assert payload["order_id"] == "12345"
    assert "approvals" in raw["context"]["payload"]


def test_normalize_human_approval_strips_resolved_by_provenance():
    out = normalize(_human_step("human-1", "rejected").output, node_type="human_approval")
    assert "resolvedBy" not in out
    assert out["decision"] == "rejected"
    assert out["target"] == "tool-x"


def test_compare_matches_when_replay_uses_preset_approval_channel():
    # 浏览器实测场景：录制时人工超时决策，回放经 inputs.approvals 预置：
    # trigger 载荷回显 approvals、approval 节点 resolvedBy 由 timeout 变 input。
    trigger_base = RecordStep(
        node_id="trigger-1",
        node_type="trigger",
        output={"context": {"triggerType": "webhook", "payload": {"order_id": "12345"}}},
    )
    trigger_replay = RecordStep(
        node_id="trigger-1",
        node_type="trigger",
        output={
            "context": {
                "triggerType": "webhook",
                "payload": {"order_id": "12345", "approvals": {"approval-1": "rejected"}},
            }
        },
    )
    human_base = _human_step("approval-1", "rejected", token="tok-a")
    human_base.output["resolvedBy"] = "timeout"
    human_replay = _human_step("approval-1", "rejected", token="tok-b")
    human_replay.output["resolvedBy"] = "input"
    report = compare(
        [trigger_base, human_base],
        [trigger_replay, human_replay],
        tools_by_node={},
        baseline_status="completed",
        replay_status="completed",
    )
    assert report["matches"] is True
    assert all(row["match"] for row in report["steps"])


# ---------- preset_approvals ----------

def test_preset_approvals_extracts_mixed_decisions():
    steps = [_human_step("human-1", "approved"), _human_step("human-2", "rejected")]
    assert preset_approvals(steps) == {"human-1": "approved", "human-2": "rejected"}


def test_preset_approvals_ignores_non_human_steps():
    steps = [_message_step("tool-1", "m-1", "2026-09-15T01:00:00+00:00")]
    assert preset_approvals(steps) == {}


# ---------- 打包 ZF：子图内审批的路径限定预置与子图产出归一化（04 §5.11） ----------

def _subgraph_step(node_id: str, graph_id: str, outputs: dict, trace: list | None = None) -> RecordStep:
    return RecordStep(
        node_id=node_id,
        node_type="subgraph",
        output={
            "mode": "subgraph",
            "graphId": graph_id,
            "status": "success",
            "outputs": outputs,
            "trace": trace or [],
        },
    )


def test_preset_approvals_recurses_into_subgraph_snapshot():
    """子图内审批经录制快照递归下潜，键为路径限定 "sub-1/human-1"（U1019）。"""
    steps = [
        _human_step("human-top", "approved"),
        _subgraph_step("sub-1", "child-1", {
            "human-1": {"mode": "human_approval", "decision": "rejected", "resolvedBy": "human"},
            "sub-2": {
                "mode": "subgraph", "graphId": "grand-1", "status": "success", "trace": [],
                "outputs": {"human-2": {"mode": "human_approval", "decision": "approved"}},
            },
        }),
    ]
    snapshots = {
        "child-1": {"nodes": [
            {"id": "human-1", "type": "human_approval"},
            {"id": "sub-2", "type": "subgraph"},
        ]},
        "grand-1": {"nodes": [{"id": "human-2", "type": "human_approval"}]},
    }
    assert preset_approvals(steps, subgraphs=snapshots) == {
        "human-top": "approved",
        "sub-1/human-1": "rejected",
        "sub-1/sub-2/human-2": "approved",
    }
    # 无快照（旧用例）→ 只取顶层，不猜子层类型
    assert preset_approvals(steps) == {"human-top": "approved"}
    # 快照缺引用 / 产出形状异常 → 该子树静默跳过，不抛错
    assert preset_approvals(steps, subgraphs={}) == {"human-top": "approved"}
    assert preset_approvals([_subgraph_step("sub-1", "child-1", {})],
                            subgraphs={"child-1": {"nodes": "bad"}}) == {}


def test_normalize_subgraph_strips_nested_runtime_values():
    """subgraph 产出按子层 mode 递归剔除运行期值，业务键原样保留（U1020）。"""
    output = {
        "mode": "subgraph", "graphId": "child-1", "status": "success",
        "trace": ["human-1: approved (human) → ok"],
        "outputs": {
            "trigger-1": {"context": {"payload": {
                "order_id": "12399", "approvals": {"human-1": "approved"}}}},
            "human-1": {"mode": "human_approval", "decision": "approved",
                        "target": "ok", "resolvedBy": "human", "token": "tok-1"},
            "ok": {"result": {"status": "SIMULATED"}, "action_status": "SUCCESS"},
        },
    }
    got = normalize(output, None, "subgraph")
    assert "trace" not in got, "子层人读日志（内嵌决策来源）不参与比对"
    assert got["outputs"]["human-1"] == {
        "mode": "human_approval", "decision": "approved", "target": "ok"}
    assert got["outputs"]["trigger-1"]["context"]["payload"] == {"order_id": "12399"}
    assert got["outputs"]["ok"] == {"result": {"status": "SIMULATED"}, "action_status": "SUCCESS"}
    # 顶层节点归一化规则不受影响（回归对照）
    assert normalize({"mode": "human_approval", "decision": "approved", "resolvedBy": "human"},
                     None, "human_approval") == {"mode": "human_approval", "decision": "approved"}


# ---------- 打包 ZG：子图内 tool 节点的归一化规则跨边界补齐（04 §5.11） ----------

def _subgraph_http_output(graph_id: str, date_value: str) -> dict:
    return {
        "mode": "subgraph", "graphId": graph_id, "status": "success", "trace": [],
        "outputs": {"http-1": _http_step("http-1", date_value).output},
    }


def test_normalize_subgraph_applies_tool_rule_via_snapshot():
    """子图内 http/request 产出经录制快照识别 tool 后删 result.headers.date（U1024）。"""
    snapshots = {"child-1": {"nodes": [
        {"id": "http-1", "type": "tool_call", "config": {"tool": "http/request"}},
    ]}}
    got = normalize(_subgraph_http_output("child-1", "Mon, 15 Sep 2026 01:00:00 GMT"),
                    None, "subgraph", subgraphs=snapshots)
    headers = got["outputs"]["http-1"]["result"]["headers"]
    assert "date" not in headers
    assert headers["content-type"] == "application/json"
    assert got["outputs"]["http-1"]["result"]["body"]["order_id"] == "12345"
    # 反向对照：无快照 → 不知子层 tool → 逐字回退旧行为（date 保留）
    raw = normalize(_subgraph_http_output("child-1", "Mon, 15 Sep 2026 01:00:00 GMT"),
                    None, "subgraph")
    assert raw["outputs"]["http-1"]["result"]["headers"]["date"]


def test_normalize_subgraph_tool_rule_only_for_http_and_recurses():
    """非 http 的 tool 不受影响；嵌套子图同口径递归（U1024）。"""
    snapshots = {
        "child-1": {"nodes": [
            {"id": "http-1", "type": "tool_call", "config": {"tool": "http/request"}},
            {"id": "msg-1", "type": "tool_call", "config": {"tool": "message/send"}},
            {"id": "sub-2", "type": "subgraph"},
        ]},
        "grand-1": {"nodes": [
            {"id": "http-2", "type": "tool_call", "config": {"tool": "http/request"}},
        ]},
    }
    output = {
        "mode": "subgraph", "graphId": "child-1", "status": "success", "trace": [],
        "outputs": {
            "http-1": _http_step("http-1", "Mon, 15 Sep 2026 01:00:00 GMT").output,
            "msg-1": _message_step("msg-1", "uuid-1", "2026-09-15T01:00:00+00:00").output,
            "sub-2": {
                "mode": "subgraph", "graphId": "grand-1", "status": "success", "trace": [],
                "outputs": {"http-2": _http_step("http-2", "Mon, 15 Sep 2026 09:00:00 GMT").output},
            },
        },
    }
    got = normalize(output, None, "subgraph", subgraphs=snapshots)
    # http 专属规则下潜到子层
    assert "date" not in got["outputs"]["http-1"]["result"]["headers"]
    # 非 http 工具不受影响：message 记录仍按 sent_at/uuid id 规则剔除，业务键保留
    assert "sent_at" not in got["outputs"]["msg-1"]["result"]
    assert "id" not in got["outputs"]["msg-1"]["result"]
    assert got["outputs"]["msg-1"]["result"]["channel"] == "email"
    # 嵌套子图按同一口径递归
    assert "date" not in got["outputs"]["sub-2"]["outputs"]["http-2"]["result"]["headers"]


def test_compare_subgraph_nested_http_date_matches_only_with_snapshot():
    """仅子层 http date 不同的两次运行：给快照才判一致（U1025，含反向对照）。"""
    snapshots = {"child-1": {"nodes": [
        {"id": "http-1", "type": "tool_call", "config": {"tool": "http/request"}},
    ]}}
    baseline = [RecordStep(node_id="sub-1", node_type="subgraph",
                           output=_subgraph_http_output("child-1", "Mon, 15 Sep 2026 01:00:00 GMT"))]
    replay = [RecordStep(node_id="sub-1", node_type="subgraph",
                         output=_subgraph_http_output("child-1", "Mon, 15 Sep 2026 09:00:00 GMT"))]
    with_snapshot = compare(baseline, replay, tools_by_node={"sub-1": None},
                            baseline_status="completed", replay_status="completed",
                            subgraphs=snapshots)
    assert with_snapshot["matches"] is True
    # 反向对照：无快照 → 不知子层 tool → 旧行为判不一致（门禁误报 blocked 的来源）
    without = compare(baseline, replay, tools_by_node={"sub-1": None},
                      baseline_status="completed", replay_status="completed")
    assert without["matches"] is False


# ---------- dedupe_steps ----------

def test_dedupe_steps_keeps_last_occurrence_and_position():
    s1 = RecordStep(node_id="a", node_type="tool_call", output={"v": 1})
    s2 = RecordStep(node_id="b", node_type="tool_call", output={"v": 2})
    s3 = RecordStep(node_id="a", node_type="tool_call", output={"v": 3})
    result = dedupe_steps([s1, s2, s3])
    assert [step.node_id for step in result] == ["b", "a"]
    assert result[-1].output == {"v": 3}


# ---------- build_tool_mocks（docs/28 §2.2 Mock 回放） ----------

def _case_with_steps(steps):
    return RecordingCase(
        id="rec-1", name="n", graph={"version": 1}, inputs={},
        steps=steps, status="completed", created_at="2026-09-20T00:00:00+00:00",
    )


def test_build_tool_mocks_filters_tool_call_and_keeps_last():
    case = _case_with_steps([
        RecordStep(node_id="t", node_type="trigger", output={"a": 1}),
        RecordStep(node_id="tool-1", node_type="tool_call", output={"result": {"v": 1}}),
        RecordStep(node_id="tool-1", node_type="tool_call", output={"result": {"v": 2}}),
        RecordStep(node_id="h", node_type="human_approval", output={"decision": "approved"}),
    ])
    mocks, mocked = build_tool_mocks(case)
    assert mocked == ["tool-1"]  # 仅 tool_call，dedupe 保末
    assert mocks == {"tool-1": {"result": {"v": 2}}}


def test_build_tool_mocks_empty_when_no_tool_steps():
    case = _case_with_steps([
        RecordStep(node_id="t", node_type="trigger", output={}),
        RecordStep(node_id="h", node_type="human_approval", output={"decision": "approved"}),
    ])
    mocks, mocked = build_tool_mocks(case)
    assert mocks == {} and mocked == []


# ---------- compare ----------

def test_compare_identical_steps_matches_with_tools_mapping():
    baseline = [
        _human_step("human-1", "approved", token="tok-a"),
        _http_step("tool-http", "Mon, 15 Sep 2026 01:00:00 GMT"),
        _message_step("tool-msg", "uuid-a", "2026-09-15T01:00:00+00:00"),
    ]
    replay = [
        _human_step("human-1", "approved", token="tok-b"),
        _http_step("tool-http", "Mon, 15 Sep 2026 09:00:00 GMT"),
        _message_step("tool-msg", "uuid-b", "2026-09-15T09:00:00+00:00"),
    ]
    report = compare(
        baseline,
        replay,
        tools_by_node={"tool-http": "http/request", "tool-msg": "message/send"},
        baseline_status="completed",
        replay_status="completed",
    )
    assert report["matches"] is True
    assert all(row["match"] for row in report["steps"])


def test_compare_tampered_output_mismatches_with_diff_keys():
    baseline = [RecordStep(node_id="tool-1", node_type="tool_call", output={"order_id": "12345", "amount": 299})]
    replay = [RecordStep(node_id="tool-1", node_type="tool_call", output={"order_id": "12346", "amount": 299})]
    report = compare(
        baseline, replay, tools_by_node={}, baseline_status="completed", replay_status="completed"
    )
    assert report["matches"] is False
    row = report["steps"][0]
    assert row["match"] is False
    assert row["diff_keys"] == ["order_id"]


def test_compare_missing_node_is_branch_drift():
    baseline = [
        RecordStep(node_id="a", node_type="tool_call", output={}),
        RecordStep(node_id="b", node_type="tool_call", output={}),
    ]
    replay = [RecordStep(node_id="a", node_type="tool_call", output={})]
    report = compare(
        baseline, replay, tools_by_node={}, baseline_status="completed", replay_status="completed"
    )
    assert report["matches"] is False
    notes = {row["node_id"]: row["note"] for row in report["steps"]}
    assert "缺少" in notes["b"]


def test_compare_extra_node_is_branch_drift():
    baseline = [RecordStep(node_id="a", node_type="tool_call", output={})]
    replay = [
        RecordStep(node_id="a", node_type="tool_call", output={}),
        RecordStep(node_id="b", node_type="tool_call", output={}),
    ]
    report = compare(
        baseline, replay, tools_by_node={}, baseline_status="completed", replay_status="completed"
    )
    assert report["matches"] is False
    assert any(row["node_id"] == "b" and "多出" in row["note"] for row in report["steps"])


def test_compare_status_mismatch_fails_even_when_steps_equal():
    step = RecordStep(node_id="a", node_type="tool_call", output={"v": 1})
    report = compare(
        [step], [step.model_copy(deep=True)], tools_by_node={},
        baseline_status="completed", replay_status="failed",
    )
    assert report["matches"] is False
    assert report["baseline_status"] == "completed"
    assert report["replay_status"] == "failed"


def test_compare_dedupes_parallel_duplicate_node_end_before_matching():
    out_a = {"mode": "parallel", "status": "success"}
    baseline = [
        RecordStep(node_id="p", node_type="parallel", output={"mode": "parallel", "status": "running"}),
        RecordStep(node_id="p", node_type="parallel", output=out_a),
    ]
    replay = [
        RecordStep(node_id="p", node_type="parallel", output={"mode": "parallel", "status": "running"}),
        RecordStep(node_id="p", node_type="parallel", output={"mode": "parallel", "status": "success"}),
    ]
    report = compare(
        baseline, replay, tools_by_node={}, baseline_status="completed", replay_status="completed"
    )
    assert report["matches"] is True
    assert len(report["steps"]) == 1


# ---------- collect_steps ----------

def test_collect_steps_keeps_node_end_last_wins():
    emit, take_steps = collect_steps()
    emit({"type": "node_start", "node_id": "a", "node_type": "tool_call"})
    emit({"type": "node_end", "node_id": "a", "node_type": "tool_call", "output": {"v": 1}})
    emit({"type": "node_end", "node_id": "a", "node_type": "tool_call", "output": {"v": 2}})
    steps = take_steps()
    assert len(steps) == 1
    assert steps[0].output == {"v": 2}


# ---------- RecordingStore ----------

def test_recording_store_crud_and_counter_ids():
    store = RecordingStore()
    graph = {"version": 1, "variables": [], "nodes": [{"id": "trigger-1", "type": "trigger"}], "edges": []}
    steps = [RecordStep(node_id="trigger-1", node_type="trigger", output={})]
    case = store.add(name="case a", graph=graph, inputs=None, steps=steps, status="completed")
    assert case.id == "rec-1"
    assert case.graph is graph or case.graph == graph
    assert case.created_at

    second = store.add(name="case b", graph=graph, inputs={"k": 1}, steps=steps, status="completed")
    assert second.id == "rec-2"
    assert [item.id for item in store.list()] == ["rec-1", "rec-2"]
    assert store.get("rec-1") is case
    assert store.get("rec-missing") is None
    assert store.delete("rec-1") is True
    assert store.get("rec-1") is None
    assert store.delete("rec-1") is False


def test_recording_store_update_meta_only_name_inputs():
    store = RecordingStore()
    graph = {"version": 1, "nodes": [{"id": "t", "type": "trigger"}]}
    steps = [RecordStep(node_id="t", node_type="trigger", output={"a": 1})]
    case = store.add(name="原名", graph=graph, inputs={"x": 1}, steps=steps,
                     status="completed", graph_id="g-1",
                     subgraphs={"s@1": {"version": 1}})

    # 仅改名
    renamed = store.update_meta(case.id, name="新名")
    assert renamed is not None and renamed.name == "新名"
    assert renamed.inputs == {"x": 1} and renamed.graph_id == "g-1"
    # 仅改 inputs；steps/graph/subgraphs/graph_id/时间戳不动
    re_input = store.update_meta(case.id, inputs={"x": 2, "y": 3})
    assert re_input is not None and re_input.inputs == {"x": 2, "y": 3}
    assert re_input.name == "新名"
    assert re_input.steps == steps and re_input.graph == graph
    assert re_input.subgraphs == {"s@1": {"version": 1}} and re_input.graph_id == "g-1"
    assert re_input.created_at == case.created_at and re_input.recorded_at == case.recorded_at
    # 读回应为更新后的同一存储项
    assert store.get(case.id).name == "新名" and store.get(case.id).inputs == {"x": 2, "y": 3}
    # 无变更字段：原样返回（不报错）
    assert store.update_meta(case.id) is store.get(case.id)
    # 不存在 → None
    assert store.update_meta("rec-missing", name="x") is None


def test_recording_create_request_validation():
    payload = {
        "name": "x" * 101,
        "graph_id": "graph-1",
        "inputs": None,
        "steps": [],
        "status": "completed",
    }
    with pytest.raises(ValueError):
        RecordingCreateRequest.model_validate(payload)
    ok = RecordingCreateRequest.model_validate({**payload, "name": "正常用例", "steps": [
        {"node_id": "a", "node_type": "trigger", "output": {}}
    ]})
    assert isinstance(ok.steps[0], RecordStep)
    assert isinstance(ok, RecordingCase) is False


# --- D26 subgraph 快照内联（collect_subgraph_snapshots / inline_first_resolver）---

def _graph_referencing(ref: str, *, sub_node_id: str = "sub-1") -> dict:
    """一张含单个 subgraph 节点引用 ref 的最小父图原始 JSON。"""
    return {
        "version": 1,
        "variables": [],
        "nodes": [
            {"id": "t", "type": "trigger", "name": "t",
             "config": {"triggerType": "manual"}},
            {"id": sub_node_id, "type": "subgraph", "name": "子流程",
             "config": {"graphId": ref, "inputs": {}}},
        ],
        "edges": [{"id": "e1", "source": "t", "target": sub_node_id}],
    }


def _leaf_graph(trigger_id: str = "t") -> dict:
    return {
        "version": 1,
        "variables": [],
        "nodes": [{"id": trigger_id, "type": "trigger", "name": "t",
                   "config": {"triggerType": "manual"}}],
        "edges": [],
    }


def test_collect_subgraph_snapshots_recurses_and_keys_by_raw_ref():
    from atlas.recording import collect_subgraph_snapshots

    grand = _leaf_graph("grand-t")
    middle = _graph_referencing("grand", sub_node_id="m-sub")
    parent = _graph_referencing("mid@2")  # 引用原文带钉版
    library = {"mid@2": middle, "grand": grand}

    snapshots = collect_subgraph_snapshots(parent, fetch_raw=library.get)

    assert set(snapshots) == {"mid@2", "grand"}
    assert snapshots["mid@2"] is middle
    assert snapshots["grand"] is grand


def test_collect_subgraph_snapshots_skips_missing_reference():
    from atlas.recording import collect_subgraph_snapshots

    parent = _graph_referencing("graph-ghost")
    snapshots = collect_subgraph_snapshots(parent, fetch_raw=lambda ref: None)
    assert snapshots == {}  # 引用缺失不阻断录制


def test_collect_subgraph_snapshots_breaks_reference_cycle():
    from atlas.recording import collect_subgraph_snapshots

    # a → b → a（跨图环，编译期会拦；收集器只负责不卡死、尽力冻结）
    a = _graph_referencing("b", sub_node_id="a-sub")
    b = _graph_referencing("a", sub_node_id="b-sub")
    library = {"a": a, "b": b}

    snapshots = collect_subgraph_snapshots(a, fetch_raw=library.get)
    assert "b" in snapshots  # 终止且有冻结，未无限递归


def test_collect_subgraph_snapshots_respects_max_depth():
    from atlas.recording import collect_subgraph_snapshots

    c1 = _graph_referencing("c2", sub_node_id="n1")
    c2 = _graph_referencing("c3", sub_node_id="n2")
    c3 = _graph_referencing("c4", sub_node_id="n3")  # 第 4 层，超出 MAX=3
    library = {"c1": c1, "c2": c2, "c3": c3}

    snapshots = collect_subgraph_snapshots(_graph_referencing("c1"), fetch_raw=library.get)
    assert set(snapshots) == {"c1", "c2", "c3"}
    assert "c4" not in snapshots


def test_inline_first_resolver_prefers_snapshot_then_falls_back():
    from atlas.recording import inline_first_resolver

    child = _leaf_graph("child-trigger")
    calls: list[str] = []

    def fallback(graph_id: str):
        calls.append(graph_id)
        return "FALLBACK"

    resolver = inline_first_resolver({"child": child}, fallback)
    parsed = resolver("child")
    assert _parsed_has_trigger(parsed, "child-trigger")
    assert calls == []  # 命中内联，未触 fallback

    assert resolver("other") == "FALLBACK"
    assert calls == ["other"]

    # 旧用例无内联（空 dict）——等价于直接走 fallback，保持历史行为
    legacy = inline_first_resolver({}, fallback)
    assert legacy("child") == "FALLBACK"


def _parsed_has_trigger(graph, node_id: str) -> bool:
    return any(node.id == node_id for node in graph.nodes)


# ---------- 打包 ZJ：event wait 的预置抽取与归一化（04 §5.11，D47） ----------

def _wait_step(
    node_id: str,
    wait_type: str = "event",
    resolved_by: str = "signal",
    payload: dict | None = None,
    signaled: bool = True,
    waited: int = 3,
    event_key: str = "order_paid",
) -> RecordStep:
    """构造 wait 步骤产出（signal 形状；timeout 形状由参数覆盖）。"""
    output: dict = {
        "mode": "wait",
        "waitType": wait_type,
        "signaled": signaled,
        "waitedSeconds": waited,
        "resolvedBy": resolved_by,
        "token": f"wait-{node_id}",
    }
    if wait_type == "event":
        output["eventKey"] = event_key
        output["payload"] = payload if payload is not None else {}
    else:
        output["durationSeconds"] = waited
        output["payload"] = {}
    return RecordStep(node_id=node_id, node_type="wait", output=output)


def test_preset_wait_events_extracts_event_wait_payloads():
    """顶层 event wait 抽 payload 为 {node_id: payload}，duration 型不抽（U1046）。"""
    from atlas.recording import preset_wait_events

    steps = [
        _wait_step("wait-1", resolved_by="signal", payload={"paidAt": "2026-09-23"}),
        _wait_step("wait-2", wait_type="duration", payload={}),
    ]
    assert preset_wait_events(steps) == {"wait-1": {"paidAt": "2026-09-23"}}


def test_preset_wait_events_unconditional_and_payload_normalization():
    """无条件预置：timeout 基线（空 payload）与非 dict payload 归一 {}，不真挂起（U1046）。"""
    from atlas.recording import preset_wait_events

    # 基线走 timeout（signaled=False、payload {}）——仍预置 {} 秒过
    timeout_steps = [_wait_step("wait-1", resolved_by="timeout", signaled=False, payload={})]
    assert preset_wait_events(timeout_steps) == {"wait-1": {}}
    # 预置值为非 dict（录制异常/字符串 payload）→ 归一 {}
    non_dict = [_wait_step("wait-1", resolved_by="signal", payload="done")]
    assert preset_wait_events(non_dict) == {"wait-1": {}}


def test_preset_wait_events_recurses_into_subgraph_snapshot():
    """子图内 event wait 经录制快照递归下潜，键为路径限定 "sub-1/wait-1"（U1046）。"""
    from atlas.recording import preset_wait_events

    steps = [
        _wait_step("wait-top", resolved_by="signal", payload={"top": 1}),
        _subgraph_step("sub-1", "child-1", {
            "wait-1": {"mode": "wait", "waitType": "event", "signaled": True,
                       "resolvedBy": "signal", "payload": {"paidAt": "2026-09-23"}},
            "sub-2": {
                "mode": "subgraph", "graphId": "grand-1", "status": "success", "trace": [],
                "outputs": {"wait-2": {"mode": "wait", "waitType": "event", "signaled": False,
                                       "resolvedBy": "timeout", "payload": {}}},
            },
        }),
    ]
    snapshots = {
        "child-1": {"nodes": [
            {"id": "wait-1", "type": "wait", "config": {"waitType": "event"}},
            {"id": "sub-2", "type": "subgraph"},
        ]},
        "grand-1": {"nodes": [
            {"id": "wait-2", "type": "wait", "config": {"waitType": "event"}},
            {"id": "dur-1", "type": "wait", "config": {"waitType": "duration"}},
        ]},
    }
    assert preset_wait_events(steps, subgraphs=snapshots) == {
        "wait-top": {"top": 1},
        "sub-1/wait-1": {"paidAt": "2026-09-23"},
        "sub-1/sub-2/wait-2": {},
    }
    # 无快照（旧用例）→ 只取顶层，不猜子层类型
    assert preset_wait_events(steps) == {"wait-top": {"top": 1}}
    # 快照缺引用 / 产出形状异常 / 快照节点缺 config → 该子树静默跳过，不抛错
    assert preset_wait_events(steps, subgraphs={}) == {"wait-top": {"top": 1}}
    assert preset_wait_events(
        [_subgraph_step("sub-1", "child-1", {})],
        subgraphs={"child-1": {"nodes": "bad"}},
    ) == {}
    assert preset_wait_events(
        [_subgraph_step("sub-1", "child-1", {"wait-1": {"mode": "wait"}})],
        subgraphs={"child-1": {"nodes": [{"id": "wait-1", "type": "wait"}]}},  # 无 config → 不抽
    ) == {}


def test_normalize_wait_event_strips_signal_echo_but_keeps_payload():
    """event wait 归一化：signal 形状与 input 预置形状对齐到同一 payload（U1047）。"""
    from atlas.recording import normalize

    signal = _wait_step("wait-1", resolved_by="signal", payload={"paidAt": "2026-09-23"},
                        event_key="order_paid", waited=7)
    signal_output = signal.output
    # 多事件 OR 竞速形状（matchedEventKey/matchedEventKeys/matchedPayloads 等信号匹配细节）
    signal_output["matchedEventKey"] = "order_paid"
    signal_output["eventKeys"] = ["order_paid", "payment_captured"]
    signal_output["eventWaitMode"] = "any"

    # 回放走 input 预置的形状（eventKey 空、signaled True、waitedSeconds 0、resolvedBy input）
    replayed = {
        "mode": "wait", "waitType": "event", "eventKey": "",
        "signaled": True, "payload": {"paidAt": "2026-09-23"},
        "waitedSeconds": 0, "resolvedBy": "input", "token": "",
    }
    assert normalize(signal_output, None, "wait") == normalize(replayed, None, "wait") == {
        "mode": "wait", "waitType": "event", "payload": {"paidAt": "2026-09-23"},
    }
    # 反向对照：不归一化时两者不同（缺陷面证据）
    assert signal_output != replayed


def test_normalize_wait_duration_unaffected():
    """duration wait 是确定性时长：不剔除运行期键、原样保留（U1047 反向对照）。"""
    from atlas.recording import normalize

    dur = _wait_step("wait-1", wait_type="duration", resolved_by="duration",
                     payload={}, signaled=False, waited=5)
    got = normalize(dur.output, None, "wait")
    assert got == {
        "mode": "wait", "waitType": "duration", "signaled": False,
        "waitedSeconds": 5, "resolvedBy": "duration",
        "durationSeconds": 5, "payload": {},
    }
