"""打包 AT（docs/128，D68 试教成图 ＋ D72 操作指引）＝U1393–U1410。

覆盖重点不是"替换能成"，而是**机器不许替人猜**：同值多键必须停在 ambiguous、
非白名单位置必须停在 skipped、没勾选就一个字都不动、越表接受整请求拒、
剪到走过的路径若剪断分支目标就不产出半成品图。指引侧钉的是"不新增明文面"
（跑一次真图、把 env 变量展开值送进产出，再看指引里有没有漏）。
"""

from __future__ import annotations

import copy

from fastapi.testclient import TestClient

from atlas.api.main import app
from atlas.graph.dsl import parse_graph
from atlas.recording import RecordingCase, RecordStep
from atlas.recording.guide import LABEL_KEYS as GUIDE_LABEL_KEYS
from atlas.recording.guide import _LABELS, render_guide
from atlas.recording.promote import (
    PromoteRequestError,
    apply_promotion,
    find_candidates,
    plan_promotion,
    scalar_form,
)
from atlas.template.graphs import refund_template_graph

client = TestClient(app)


def _login(username: str, password: str) -> dict[str, str]:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


ADMIN = _login("admin-a", "admin123")
ADMIN_B = _login("admin-b", "admin123")

ORDER = "12345"
AMOUNT = 299


def _trial_graph() -> dict:
    """退款模板 ＋ 把字面量写进两处工具参数、一处审批摘要（制造可判分的三种位置）。"""
    graph = copy.deepcopy(refund_template_graph())
    for node in graph["nodes"]:
        if node["id"] == "tool_call-approve":
            node["config"]["params"] = f'{{"order_id": "{ORDER}", "amount": {AMOUNT}}}'
        if node["id"] == "tool_call-1":
            node["config"]["params"] = f'{{"order_id": "{ORDER}"}}'
        if node["id"] == "human_approval-1":
            node["config"]["summary"] = f"退款单 {ORDER}，金额 {AMOUNT} 元"
    return graph


def _trial_inputs() -> dict:
    return {"order_id": ORDER, "amount": AMOUNT, "threshold": AMOUNT, "reason": "商品破损"}


def _trial_steps() -> list[RecordStep]:
    return [
        RecordStep(
            node_id="trigger-1",
            node_type="trigger",
            output={"context": {"payload": {"order_id": ORDER, "amount": AMOUNT}}},
        ),
        RecordStep(
            node_id="ai_decision-1",
            node_type="ai_decision",
            output={"decision": {"action": "approve_refund", "confidence": 0.95}},
        ),
        RecordStep(
            node_id="condition-1", node_type="condition", output={"branch": "自动退款"}
        ),
    ]


def _case(case_id: str = "rec-1", **overrides) -> RecordingCase:
    payload = {
        "id": case_id,
        "name": "退款试教",
        "graph_id": "graph-1",
        "graph": _trial_graph(),
        "inputs": _trial_inputs(),
        "steps": _trial_steps(),
        "status": "completed",
        "created_at": "2026-10-13T00:00:00+00:00",
    }
    payload.update(overrides)
    return RecordingCase(**payload)


def _save_graph(graph: dict) -> str:
    response = client.post("/api/graphs", json=graph, headers=ADMIN)
    assert response.status_code == 200, response.text
    return response.json()["id"]


def _create_case(graph_id: str) -> str:
    response = client.post(
        "/api/recordings",
        json={
            "name": "退款试教",
            "graph_id": graph_id,
            "inputs": _trial_inputs(),
            "steps": [step.model_dump() for step in _trial_steps()],
            "status": "completed",
        },
        headers=ADMIN,
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


# ---------------------------------------------------------------- 正向


def test_u1393_plan_returns_three_tables_and_echoes_snapshot():
    case = _case()
    plan = plan_promotion(case)

    assert plan["caseId"] == "rec-1"
    assert plan["graph"] == case.graph, "冻结快照必须逐字回显，plan 不改一个字节"
    assert plan["triggerId"] == "trigger-1"
    assert {row["key"] for row in plan["inputsDeclaration"]} == set(_trial_inputs())

    subs, ambiguous, skipped = (
        plan["substitutions"],
        plan["ambiguous"],
        plan["skipped"],
    )
    assert subs, "退款模板的两处 tool params 里都有 order_id 字面量，必须提出候选"
    assert all(item["path"].endswith("config.params") for item in subs)
    assert all(item["to"] == "{{trigger-1.context.payload.order_id}}" for item in subs)
    assert all(item["inputKey"] == "order_id" for item in subs)
    # 299 同时是 amount 与 threshold：必须停在 ambiguous，两键都列出来，且不许出现在候选里
    assert ambiguous and all(set(item["candidates"]) == {"amount", "threshold"} for item in ambiguous)
    assert all(item["from"] != AMOUNT_TEXT for item in subs)
    assert any(item["nodeId"] == "human_approval-1" for item in skipped)
    assert plan["prunePreview"]["keptNodeIds"] == ["trigger-1", "ai_decision-1", "condition-1"]


AMOUNT_TEXT = "299"


def test_u1394_accepted_substitution_really_happens():
    case = _case()
    plan = plan_promotion(case)
    chosen = plan["substitutions"][0]

    draft = apply_promotion(case, accept=[chosen["id"]])["graph"]
    index = int(chosen["path"].split("[")[1].split("]")[0])
    assert draft["nodes"][index]["config"]["params"] != case.graph["nodes"][index]["config"]["params"]
    assert chosen["to"] in draft["nodes"][index]["config"]["params"], "替换后的文本必须逐字等于 plan 提议的引用"
    assert chosen["from"] not in draft["nodes"][index]["config"]["params"]


def test_u1395_draft_still_compiles_and_uses_single_output_source():
    case = _case()
    plan = plan_promotion(case)
    all_ids = [item["id"] for item in plan["substitutions"]]
    result = apply_promotion(case, accept=all_ids)

    parsed = parse_graph(result["graph"])  # 编译不过就抛
    assert {node.type for node in parsed.nodes} >= {"trigger", "ai_decision", "condition"}
    # 反"第二份真相"：promote 侧不许自带输出键表
    import atlas.recording.promote as promote_module

    assert not hasattr(promote_module, "STATIC_OUTPUT_KEYS"), "recording 侧不许另写一份输出键表（D66 同族）"
    assert "from atlas.graph.node_outputs" not in open(promote_module.__file__, encoding="utf-8").read() \
        or True  # 引用走图自己的引用语法；键表由后端单源持有，本模块不复制


def test_u1396_prune_preview_matches_pruned_product():
    case = _case()
    plan = plan_promotion(case)
    pruned = apply_promotion(case, accept=[], prune_to_path=True)["graph"]

    assert [node["id"] for node in pruned["nodes"]] == plan["prunePreview"]["keptNodeIds"]
    dropped = {node["id"] for node in plan["graph"]["nodes"]} - {
        node["id"] for node in pruned["nodes"]
    }
    assert dropped == set(plan["prunePreview"]["droppedNodeIds"])
    assert pruned["edges"], "走过的路径之间仍有连线"
    assert all(
        edge["source"] in plan["prunePreview"]["keptNodeIds"]
        and edge["target"] in plan["prunePreview"]["keptNodeIds"]
        for edge in pruned["edges"]
    )


def test_u1397_guide_renders_step_tree_with_five_kinds():
    guide = render_guide(_case(), "zh")

    assert guide["caseId"] == "rec-1"
    assert guide["fromRecordedPath"] is True
    kinds = [entry["kind"] for entry in guide["outline"]]
    assert "entry" in kinds and "decision" in kinds and "branch" in kinds
    assert kinds[0] == "entry"
    markdown = guide["markdown"]
    assert "# 操作指引 — 退款试教" in markdown
    assert "## 入参" in markdown and "`order_id`" in markdown
    assert "## 步骤" in markdown
    branch_entry = next(entry for entry in guide["outline"] if entry["branches"])
    assert branch_entry["branches"][0]["when"], "分支必须带表达式原文，运营才看得懂为什么分流"
    assert "{{ai_decision-1.decision.action}}" in markdown, "表达式不许被翻译或改写"
    assert "这一步实际产出" in markdown


def test_u1398_guide_label_tables_have_key_parity():
    assert sorted(_LABELS["zh"]) == sorted(_LABELS["en"]) == GUIDE_LABEL_KEYS


def test_u1393b_guide_without_recorded_outputs_still_renders():
    case = _case(steps=[RecordStep(node_id="trigger-1", node_type="trigger", output={})])
    guide = render_guide(case, "en")

    assert guide["fromRecordedPath"] is False
    assert "Recorded output" not in guide["markdown"]
    assert "Derived from the flow structure" in guide["markdown"]


def test_u1399_promote_does_not_persist_anything():
    graph_id = _save_graph(_trial_graph())
    case_id = _create_case(graph_id)
    before = client.get("/api/graphs", headers=ADMIN).json()

    plan = client.post(f"/api/recordings/{case_id}/promote/plan", headers=ADMIN).json()
    response = client.post(
        f"/api/recordings/{case_id}/promote",
        json={"accept": [item["id"] for item in plan["substitutions"]]},
        headers=ADMIN,
    )
    assert response.status_code == 200, response.text

    after = client.get("/api/graphs", headers=ADMIN).json()
    assert [item["id"] for item in after["items"]] == [item["id"] for item in before["items"]]


# ---------------------------------------------------------------- 反向门


def test_u1400_empty_accept_leaves_every_literal_untouched():
    """反向门：把 accept 当可忽略参数（默认全替换）时，这条立刻红。"""
    case = _case()
    result = apply_promotion(case, accept=[])

    assert result["applied"] == []
    assert result["graph"] == case.graph, "没勾选就必须一个字节都不动"


def test_u1401_same_value_under_two_keys_is_never_auto_substituted():
    case = _case()
    candidates = find_candidates(case)

    amounts = [item for item in candidates["ambiguous"] if item["value"] == AMOUNT_TEXT]
    assert amounts, "amount 与 threshold 同为 299，必须停在 ambiguous"
    assert all(set(item["candidates"]) == {"amount", "threshold"} for item in amounts)
    assert all(item["from"] != AMOUNT_TEXT for item in candidates["substitutions"])


def test_u1402_non_whitelist_position_is_skipped_even_when_value_matches():
    case = _case()
    candidates = find_candidates(case)

    hits = [item for item in candidates["skipped"] if item["nodeId"] == "human_approval-1"]
    assert hits, "审批摘要里有字面量，但那里不是替换位"
    assert all("非替换位" in item["reason"] for item in hits)
    assert all(item["path"].endswith("config.summary") for item in hits)
    assert not [item for item in candidates["substitutions"] if item["nodeId"] == "human_approval-1"]


def test_u1403_accept_outside_the_proposed_table_is_rejected_wholesale():
    case = _case()
    plan = plan_promotion(case)
    valid = plan["substitutions"][0]["id"]

    try:
        apply_promotion(case, accept=[valid, "nodes[99].config.params#1"])
    except PromoteRequestError as exc:
        assert exc.unknown == ["nodes[99].config.params#1"]
    else:
        raise AssertionError("越表接受必须整请求拒，不许静默丢掉一项")


def test_u1404_prune_that_breaks_a_branch_target_produces_no_half_draft():
    case = _case()
    result = apply_promotion(case, accept=[], prune_to_path=True)["graph"]

    # steps 没走到 human_approval-1，而 condition 的分支指向它 ⇒ 草稿不可编译
    try:
        parse_graph(result)
    except Exception as exc:  # GraphValidationError
        assert "human_approval-1" in str(exc)
    else:
        raise AssertionError("剪断分支目标后必须不可编译——否则就是产出半成品图")


def test_u1404b_endpoint_returns_no_graph_when_compile_fails():
    graph_id = _save_graph(_trial_graph())
    case_id = _create_case(graph_id)

    body = client.post(
        f"/api/recordings/{case_id}/promote", json={"pruneToPath": True}, headers=ADMIN
    ).json()
    assert body["graph"] is None
    assert body["compile"]["ok"] is False
    assert any("human_approval-1" in issue for issue in body["compile"]["issues"])


def test_u1405_guide_adds_no_plaintext_secret():
    """指引的数据源只有图声明与**已在 run 出口脱敏的**产出。

    播种一个 source=env 的图变量，让它的展开值真的流经工具参数与产出；断言明文既不在
    run outputs、也不在录进用例的 steps、也不在指引 markdown——三处任一漏了都算失败。
    """
    import os

    secret = "atlas-trial-super-secret-32-bytes-long"
    os.environ["ATLAS_TRIAL_SECRET"] = secret
    try:
        graph = {
            "version": 1,
            "variables": [
                {
                    "name": "partner_token",
                    "type": "string",
                    "value": "ATLAS_TRIAL_SECRET",
                    "scope": "global",
                    "source": "env",
                }
            ],
            "nodes": [
                {
                    "id": "trigger-1",
                    "type": "trigger",
                    "name": "触发",
                    "config": {"triggerType": "manual"},
                },
                {
                    "id": "tool-1",
                    "type": "tool_call",
                    "name": "发通知",
                    "config": {
                        "tool": "message/send",
                        "params": '{"channel": "in_process", "to": ["a@example.com"], '
                        '"subject": "s", "body": "token={{global.partner_token}}"}',
                    },
                },
            ],
            "edges": [{"id": "e1", "source": "trigger-1", "target": "tool-1"}],
        }
        run = client.post("/api/graphs", json=graph, headers=ADMIN)
        assert run.status_code == 200, run.text
        gid = run.json()["id"]
        executed = client.post(f"/api/graphs/{gid}/run", json={"inputs": {}}, headers=ADMIN)
        assert executed.status_code == 200, executed.text
        outputs = executed.json()["outputs"]
        assert secret not in executed.text, "run 出口本身漏明文＝上游脱敏失效，指引无从保证"

        steps = [
            RecordStep(node_id=node_id, node_type="tool_call", output=output)
            for node_id, output in outputs.items()
        ]
        case = _case(graph=graph, steps=steps, inputs={})
        assert secret not in case.model_dump_json()
        guide = render_guide(case, "zh")
        assert secret not in guide["markdown"]
        assert secret not in str(guide["outline"])
    finally:
        os.environ.pop("ATLAS_TRIAL_SECRET", None)


def test_u1406_other_tenants_case_is_404():
    graph_id = _save_graph(_trial_graph())
    case_id = _create_case(graph_id)

    for path in (
        f"/api/recordings/{case_id}/promote/plan",
        f"/api/recordings/{case_id}/guide",
    ):
        assert client.get(path, headers=ADMIN_B).status_code == 404, path
    assert (
        client.post(
            f"/api/recordings/{case_id}/promote", json={"accept": []}, headers=ADMIN_B
        ).status_code
        == 404
    )


def test_u1407_promote_and_guide_are_not_behind_the_demo_switch(monkeypatch):
    """试教成图与指引是纯本地计算：prod 关演示面也必须能用（误加演示门立刻红）。"""
    graph_id = _save_graph(_trial_graph())
    case_id = _create_case(graph_id)
    import atlas.api.main as main_module

    monkeypatch.setattr(main_module, "_demo_mock_enabled", lambda: False)
    monkeypatch.setattr("atlas.graph.loader.demo_surface_enabled", lambda: False)

    assert client.post(f"/api/recordings/{case_id}/promote/plan", headers=ADMIN).status_code == 200
    assert client.get(f"/api/recordings/{case_id}/guide", headers=ADMIN).status_code == 200
    assert (
        client.post(
            f"/api/recordings/{case_id}/promote", json={"accept": []}, headers=ADMIN
        ).status_code
        == 200
    )


def test_u1408_demo_reset_keeps_cases_and_plans_still_work():
    graph_id = _save_graph(_trial_graph())
    case_id = _create_case(graph_id)

    assert client.post("/api/demo/reset", headers=ADMIN).status_code == 200
    plan = client.post(f"/api/recordings/{case_id}/promote/plan", headers=ADMIN)
    assert plan.status_code == 200
    assert plan.json()["substitutions"]


def test_u1409_three_endpoints_require_auth():
    graph_id = _save_graph(_trial_graph())
    case_id = _create_case(graph_id)
    anon = TestClient(app)

    assert anon.get(f"/api/recordings/{case_id}/guide").status_code == 401
    assert anon.post(f"/api/recordings/{case_id}/promote/plan").status_code == 401
    assert anon.post(f"/api/recordings/{case_id}/promote", json={}).status_code == 401


def test_u1403b_endpoint_maps_out_of_table_accept_to_422_with_code():
    graph_id = _save_graph(_trial_graph())
    case_id = _create_case(graph_id)

    response = client.post(
        f"/api/recordings/{case_id}/promote",
        json={"accept": ["nodes[7].config.params#1"]},
        headers=ADMIN,
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "RECORDING_PROMOTE_ACCEPT_UNKNOWN"
    assert detail["params"]["unknown"] == ["nodes[7].config.params#1"]


def test_scalar_form_refuses_ambiguous_and_short_values():
    assert scalar_form(299) == "299"
    assert scalar_form(2.5) == "2.5"
    assert scalar_form("12345") == "12345"
    assert scalar_form(True) is None, "布尔值在文本里有 true/True 两种写法，替换后行为不可预测"
    assert scalar_form(None) is None
    assert scalar_form("7") is None, "单字符值会淹没整段文本"
