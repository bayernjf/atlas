"""U28：录制用例存储 / 归一化 / 审批预置 / 回放比对纯逻辑（04 §5.11，06 §6.9）。"""

import pytest

from atlas.recording import (
    RecordStep,
    RecordingCase,
    RecordingCreateRequest,
    RecordingStore,
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


# ---------- dedupe_steps ----------

def test_dedupe_steps_keeps_last_occurrence_and_position():
    s1 = RecordStep(node_id="a", node_type="tool_call", output={"v": 1})
    s2 = RecordStep(node_id="b", node_type="tool_call", output={"v": 2})
    s3 = RecordStep(node_id="a", node_type="tool_call", output={"v": 3})
    result = dedupe_steps([s1, s2, s3])
    assert [step.node_id for step in result] == ["b", "a"]
    assert result[-1].output == {"v": 3}


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
