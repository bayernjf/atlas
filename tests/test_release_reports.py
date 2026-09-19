"""D26 报告 v1（U60 存储层）：批量回放报告沉淀 ring（recording/reports.py）。

批 2 覆盖 ReportStore 纯进程内语义（REST/沉淀接线见同文件批 3 API 测试）：
- record 沉淀 GateReport 同形 dict → ReleaseReport（id rr-N、trigger、pass_rate、created_at）；
- total=0 skipped 也沉淀、pass_rate=None；
- list_summary 倒序、按图过滤、不含 cases；get 跨图/不存在返 None；
- ring 满后最旧淘汰（id 计数不回收）；reset 清空并归零计数。
"""

from __future__ import annotations

import copy
import json

import pytest
from fastapi.testclient import TestClient

from atlas.api.main import app
from atlas.iam.deps import tenant_registry
from atlas.recording.reports import ReportStore

client = TestClient(app)


@pytest.fixture(autouse=True)
def _admin_session():
    token = client.post(
        "/api/auth/login", json={"username": "admin-a", "password": "admin123"}
    ).json()["token"]
    client.headers["Authorization"] = f"Bearer {token}"
    client.post("/api/demo/reset")
    # 录制用例 reset 不清除（测试资产），本文件每例清空用例存储做隔离（不改变产品 reset 语义）。
    tenant_registry.get("t1").recording_store._items.clear()
    yield
    client.headers.pop("authorization", None)


def _load_template_graph() -> dict:
    return client.get("/api/templates/approval-timeout-reject").json()["graph"]


def _record_case(graph_id: str, graph: dict, name: str) -> None:
    """跑一次 rejected 终态（预置审批秒回），用 outputs 造 steps 并绑定 graph_id 录为用例。"""
    run = client.post(
        f"/api/graphs/{graph_id}/run",
        json={"inputs": {"approvals": {"approval-1": "rejected"}}},
    )
    assert run.status_code == 200, run.text
    result = run.json()
    node_types = {node["id"]: node["type"] for node in graph["nodes"]}
    steps = [
        {"node_id": node_id, "node_type": node_types[node_id], "output": output}
        for node_id, output in result["outputs"].items()
    ]
    response = client.post(
        "/api/recordings",
        json={
            "name": name,
            "graph_id": graph_id,
            "inputs": {"approvals": {"approval-1": "rejected"}},
            "steps": steps,
            "status": result["status"],
        },
    )
    assert response.status_code == 201, response.text


def _make_graph_with_cases(case_count: int = 1) -> tuple[str, dict]:
    graph = _load_template_graph()
    graph_id = client.post("/api/graphs", json=graph).json()["id"]
    for index in range(case_count):
        _record_case(graph_id, graph, f"报告用例 {index + 1}")
    return graph_id, graph


def _tamper_draft(graph_id: str, graph: dict) -> dict:
    """把草稿 rejected-msg 的邮件正文改坏（其余不动）。"""
    bad = copy.deepcopy(graph)
    node = next(item for item in bad["nodes"] if item["id"] == "rejected-msg")
    params = json.loads(node["config"]["params"])
    params["body"] = "草稿改坏后的正文，与录制基线不一致。"
    node["config"]["params"] = json.dumps(params, ensure_ascii=False)
    tenant_registry.get("t1").graph_store._graphs[graph_id] = bad
    return bad


# ---------- U60 ① manual 门禁沉淀 ----------

def test_manual_gate_persists_report_with_id_and_fields():
    graph_id, _ = _make_graph_with_cases(case_count=2)

    response = client.post(f"/api/graphs/{graph_id}/release-gate")
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["id"].startswith("rr-")  # GateReport 纯超集加 id
    assert report["trigger"] == "manual"
    assert report["pass_rate"] == 1.0
    assert report["created_at"]
    assert len(report["cases"]) == 2

    listing = client.get(f"/api/graphs/{graph_id}/release-reports").json()["items"]
    assert len(listing) == 1
    assert listing[0]["id"] == report["id"]
    assert listing[0]["trigger"] == "manual"

    detail = client.get(f"/api/graphs/{graph_id}/release-reports/{report['id']}").json()
    assert detail["graph_id"] == graph_id
    assert len(detail["cases"]) == 2  # 详情含逐例


# ---------- U60 ② publish gate 通过/blocked 均沉淀 ----------

def test_publish_gate_persists_on_block_and_pass():
    graph_id, graph = _make_graph_with_cases(case_count=1)
    good = copy.deepcopy(graph)
    _tamper_draft(graph_id, graph)

    blocked = client.post(f"/api/graphs/{graph_id}/publish", json={"gate": True})
    assert blocked.status_code == 409
    blocked_report = blocked.json()["detail"]["report"]
    assert blocked_report["id"].startswith("rr-")  # 409 报告体也带 id
    assert blocked_report["trigger"] == "publish-gate"
    assert blocked_report["blocked"] is True

    tenant_registry.get("t1").graph_store._graphs[graph_id] = good
    ok = client.post(f"/api/graphs/{graph_id}/publish", json={"gate": True})
    assert ok.status_code == 200, ok.text

    items = client.get(f"/api/graphs/{graph_id}/release-reports").json()["items"]
    assert [item["trigger"] for item in items] == ["publish-gate", "publish-gate"]  # 倒序
    assert items[0]["blocked"] is False and items[0]["pass_rate"] == 1.0
    assert items[1]["blocked"] is True and items[1]["pass_rate"] == 0.0


# ---------- U60 ③ skipped 也沉淀、pass_rate=null ----------

def test_skipped_zero_case_run_persists_with_null_pass_rate():
    graph_id = client.post("/api/graphs", json=_load_template_graph()).json()["id"]
    report = client.post(f"/api/graphs/{graph_id}/release-gate").json()
    assert report["id"].startswith("rr-")
    assert report["skipped"] is True
    assert report["pass_rate"] is None

    items = client.get(f"/api/graphs/{graph_id}/release-reports").json()["items"]
    assert len(items) == 1 and items[0]["pass_rate"] is None and items[0]["skipped"] is True


# ---------- U60 ④ 列表摘要/详情/404 ----------

def test_list_is_summary_reverse_order_and_detail_scoped():
    graph_id, _ = _make_graph_with_cases(case_count=1)
    client.post(f"/api/graphs/{graph_id}/release-gate")
    client.post(f"/api/graphs/{graph_id}/release-gate")
    items = client.get(f"/api/graphs/{graph_id}/release-reports").json()["items"]
    assert [item["id"] for item in items] == ["rr-2", "rr-1"]  # 倒序
    assert all("cases" not in item for item in items)  # 摘要不含 cases

    detail = client.get(f"/api/graphs/{graph_id}/release-reports/rr-1").json()
    assert len(detail["cases"]) == 1

    # 报告不属于该图：另一张图取 rr-1 → 404（不泄漏存在性）
    other_id = client.post("/api/graphs", json=_load_template_graph()).json()["id"]
    assert client.get(f"/api/graphs/{other_id}/release-reports/rr-1").status_code == 404
    assert client.get(f"/api/graphs/{graph_id}/release-reports/rr-999").status_code == 404
    # 图不存在 → 404
    assert client.get("/api/graphs/graph-999999/release-reports").status_code == 404
    assert client.get("/api/graphs/graph-999999/release-reports/rr-1").status_code == 404


# ---------- U60 ④ 跨租户 404 ----------

def test_cross_tenant_reports_not_visible():
    graph_id, _ = _make_graph_with_cases(case_count=1)
    report = client.post(f"/api/graphs/{graph_id}/release-gate").json()

    token_b = client.post(
        "/api/auth/login", json={"username": "admin-b", "password": "admin123"}
    ).json()["token"]
    client.headers["Authorization"] = f"Bearer {token_b}"
    client.post("/api/demo/reset")  # 清 t2 其他用例遗留（id 计数 per-tenant，避免同 id 图干扰）
    # t2 视角该图不存在 → 列表/详情均 404，不泄漏报告存在性
    assert client.get(f"/api/graphs/{graph_id}/release-reports").status_code == 404
    assert (
        client.get(f"/api/graphs/{graph_id}/release-reports/{report['id']}").status_code
        == 404
    )


# ---------- U60 ⑤ viewer 可读不可跑 ----------

def test_viewer_can_read_history_but_not_run_gate():
    graph_id, _ = _make_graph_with_cases(case_count=1)
    client.post(f"/api/graphs/{graph_id}/release-gate")

    viewer_token = client.post(
        "/api/auth/login", json={"username": "viewer-a", "password": "viewer123"}
    ).json()["token"]
    client.headers["Authorization"] = f"Bearer {viewer_token}"
    assert client.get(f"/api/graphs/{graph_id}/release-reports").status_code == 200
    assert client.get(f"/api/graphs/{graph_id}/release-reports/rr-1").status_code == 200
    assert client.post(f"/api/graphs/{graph_id}/release-gate").status_code == 403


# ---------- U60 ⑥ reset 清空报告、保留录制用例 ----------

def test_reset_clears_reports_but_keeps_recorded_cases():
    graph_id, _ = _make_graph_with_cases(case_count=1)
    client.post(f"/api/graphs/{graph_id}/release-gate")
    services = tenant_registry.get("t1")
    assert services.report_store.list_summary(graph_id)
    case_count = len(services.recording_store.list())
    assert case_count >= 1

    client.post("/api/demo/reset")
    assert services.report_store.list_summary(graph_id) == []  # 报告是运行产物，清空
    assert len(services.recording_store.list()) == case_count  # 录制用例是测试资产，保留


# ---------- 存储层纯单元测试 ----------

def _gate_report(total: int, passed: int, *, blocked: bool | None = None) -> dict:
    """构造 GateReport 同形 dict（skipped/blocked 口径同 gate.run_release_gate）。"""
    failed = total - passed
    return {
        "graph_id": "graph-a",
        "target": "draft",
        "total": total,
        "passed": passed,
        "failed": failed,
        "skipped": total == 0,
        "blocked": failed > 0 if blocked is None else blocked,
        "cases": [
            {
                "case_id": f"rec-{i}",
                "name": f"用例 {i}",
                "matches": i < passed,
                "replay_status": "succeeded",
                "note": "全部节点一致" if i < passed else "节点 output-1 不一致",
            }
            for i in range(total)
        ],
    }


def test_record_persists_superset_with_id_and_pass_rate():
    store = ReportStore()
    saved = store.record(graph_id="graph-a", trigger="manual", report=_gate_report(4, 3))

    assert saved["id"] == "rr-1"
    assert saved["graph_id"] == "graph-a"
    assert saved["target"] == "draft"
    assert saved["trigger"] == "manual"
    assert (saved["total"], saved["passed"], saved["failed"]) == (4, 3, 1)
    assert saved["skipped"] is False
    assert saved["blocked"] is True
    assert saved["pass_rate"] == 0.75
    assert len(saved["cases"]) == 4
    assert saved["cases"][0]["case_id"] == "rec-0"
    assert saved["created_at"]

    second = store.record(graph_id="graph-a", trigger="publish-gate", report=_gate_report(2, 2))
    assert second["id"] == "rr-2"
    assert second["trigger"] == "publish-gate"
    assert second["pass_rate"] == 1.0
    assert second["blocked"] is False


def test_skipped_zero_cases_persists_with_null_pass_rate():
    store = ReportStore()
    saved = store.record(graph_id="graph-empty", trigger="manual", report=_gate_report(0, 0))

    assert saved["id"] == "rr-1"
    assert saved["total"] == 0
    assert saved["skipped"] is True
    assert saved["blocked"] is False
    assert saved["pass_rate"] is None
    assert saved["cases"] == []
    # skipped 也留痕：列表里看得到「当时未覆盖」
    assert len(store.list_summary("graph-empty")) == 1


def test_list_summary_reverse_order_filtered_without_cases():
    store = ReportStore()
    store.record(graph_id="graph-a", trigger="manual", report=_gate_report(2, 1))
    store.record(graph_id="graph-b", trigger="manual", report=_gate_report(1, 1))
    store.record(graph_id="graph-a", trigger="publish-gate", report=_gate_report(2, 2))

    summary = store.list_summary("graph-a")
    assert [item["id"] for item in summary] == ["rr-3", "rr-1"]  # 倒序
    assert all("cases" not in item for item in summary)
    assert summary[0]["pass_rate"] == 1.0
    assert summary[1]["pass_rate"] == 0.5
    assert [item["id"] for item in store.list_summary("graph-b")] == ["rr-2"]


def test_get_detail_scoped_by_graph():
    store = ReportStore()
    saved = store.record(graph_id="graph-a", trigger="manual", report=_gate_report(2, 1))

    detail = store.get("graph-a", saved["id"])
    assert detail is not None
    assert detail["id"] == saved["id"]
    assert len(detail["cases"]) == 2  # 详情含 cases

    assert store.get("graph-b", saved["id"]) is None  # 跨图不泄漏
    assert store.get("graph-a", "rr-999") is None


def test_ring_evicts_oldest_but_keeps_counter():
    store = ReportStore(maxlen=3)
    for i in range(4):
        store.record(graph_id="graph-a", trigger="manual", report=_gate_report(1, 1))

    summary = store.list_summary("graph-a")
    assert [item["id"] for item in summary] == ["rr-4", "rr-3", "rr-2"]  # rr-1 淘汰
    assert store.get("graph-a", "rr-1") is None
    assert store.get("graph-a", "rr-4") is not None


def test_reset_clears_reports_and_counter():
    store = ReportStore()
    store.record(graph_id="graph-a", trigger="manual", report=_gate_report(1, 1))
    store.reset()

    assert store.list_summary("graph-a") == []
    again = store.record(graph_id="graph-a", trigger="manual", report=_gate_report(1, 1))
    assert again["id"] == "rr-1"  # 计数归零


# ---------- D26 报告导出 CSV/JSON ----------

def test_report_to_csv_renders_meta_header_and_case_rows():
    from atlas.recording.reports import report_to_csv

    store = ReportStore()
    saved = store.record(graph_id="graph-a", trigger="manual", report=_gate_report(2, 1))
    csv_text = report_to_csv(saved)

    assert "报告ID" in csv_text and saved["id"] in csv_text
    assert "触发方式" in csv_text and "手动门禁" in csv_text
    assert "通过率" in csv_text and "50.00%" in csv_text  # 1/2
    assert "用例ID,用例名,是否匹配,回放状态,备注" in csv_text
    assert "rec-0" in csv_text and "全部节点一致" in csv_text
    # 逐例匹配结果布尔中文化：1 通过（是）+ 1 不通过（否）
    assert "rec-0,用例 0,是,succeeded" in csv_text
    assert "rec-1,用例 1,否,succeeded" in csv_text


def test_report_to_csv_marks_uncovered_when_no_cases():
    from atlas.recording.reports import report_to_csv

    store = ReportStore()
    saved = store.record(graph_id="graph-a", trigger="publish-gate", report=_gate_report(0, 0))
    csv_text = report_to_csv(saved)
    assert "未覆盖" in csv_text
    assert "发布门禁" in csv_text


def test_export_report_csv_and_json_endpoints():
    graph_id, _ = _make_graph_with_cases(case_count=1)
    rid = client.post(f"/api/graphs/{graph_id}/release-gate").json()["id"]

    csv_resp = client.get(f"/api/graphs/{graph_id}/release-reports/{rid}/export?format=csv")
    assert csv_resp.status_code == 200, csv_resp.text
    assert csv_resp.headers["content-type"].startswith("text/csv")
    assert f'attachment; filename="{rid}.csv"' in csv_resp.headers["content-disposition"]
    assert csv_resp.content.startswith(b"\xef\xbb\xbf")  # UTF-8 BOM（Excel 中文）
    assert rid in csv_resp.content.decode("utf-8-sig")

    json_resp = client.get(f"/api/graphs/{graph_id}/release-reports/{rid}/export?format=json")
    assert json_resp.status_code == 200
    assert json_resp.headers["content-type"].startswith("application/json")
    assert f'attachment; filename="{rid}.json"' in json_resp.headers["content-disposition"]
    exported = json_resp.json()
    assert exported["id"] == rid and len(exported["cases"]) == 1


def test_export_report_defaults_to_csv_and_validates_format():
    graph_id, _ = _make_graph_with_cases(case_count=0)
    rid = client.post(f"/api/graphs/{graph_id}/release-gate").json()["id"]

    default = client.get(f"/api/graphs/{graph_id}/release-reports/{rid}/export")
    assert default.status_code == 200
    assert default.headers["content-type"].startswith("text/csv")  # 默认 csv

    bad = client.get(f"/api/graphs/{graph_id}/release-reports/{rid}/export?format=xlsx")
    assert bad.status_code == 422  # format 仅 csv/json


def test_export_report_404_scoped_like_detail():
    graph_id, _ = _make_graph_with_cases(case_count=0)
    # 报告不存在
    assert client.get(f"/api/graphs/{graph_id}/release-reports/rr-999/export").status_code == 404
    # 图不存在
    assert client.get("/api/graphs/graph-ghost/release-reports/rr-1/export").status_code == 404
