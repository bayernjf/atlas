"""D26 报告 v1（U60 存储层）：批量回放报告沉淀 ring（recording/reports.py）。

批 2 覆盖 ReportStore 纯进程内语义（REST/沉淀接线见同文件批 3 API 测试）：
- record 沉淀 GateReport 同形 dict → ReleaseReport（id rr-N、trigger、pass_rate、created_at）；
- total=0 skipped 也沉淀、pass_rate=None；
- list_summary 倒序、按图过滤、不含 cases；get 跨图/不存在返 None；
- ring 满后最旧淘汰（id 计数不回收）；reset 清空并归零计数。
"""

from __future__ import annotations

from atlas.recording.reports import ReportStore


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
