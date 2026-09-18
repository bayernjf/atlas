# -*- coding: utf-8 -*-
"""D26 录制用例集报告 v1 全链路冒烟（真实 :8000，admin-a）。

纯 HTTP 驱动，为前端 ReleaseModal 历史报告折叠区造数并断言后端契约
（docs/13 U60 ①-⑦、03 release_report、04 §5.11 末）：

图 A（有 1 条黄金用例）：
  好草稿手动门禁通过（manual 沉淀 rr-1）
  → PUT 坏草稿手动门禁 blocked（manual rr-2）
  → 带门禁发布坏草稿 409（publish-gate rr-3，409 体带 id、不产版本）
  → 恢复好草稿带门禁发布 v1（publish-gate rr-4）
  → 列表倒序/摘要无 cases/详情有 cases/跨图 404。
图 B（无录制用例）：
  手动门禁 skipped（rr-1，pass_rate=null，未覆盖也沉淀）。
reset 语义：清空报告、保留录制用例（U60 ⑥）。

用法：
  先起后端：.venv/bin/uvicorn atlas.api.main:app --port 8000
  再跑：    .venv/bin/python scripts/dev/d26_smoke.py
"""
from __future__ import annotations

import json
import sys

import httpx

BASE_API = "http://localhost:8000"


def ok(cond: bool, msg: str) -> None:
    print(f"  {'OK ' if cond else 'FAIL '} {msg}")
    if not cond:
        raise AssertionError(msg)


def message_graph(tool: str) -> dict:
    """trigger(webhook) → message 工具调用；好/坏只差 tool 是否注册。"""
    return {
        "version": 1,
        "variables": [],
        "nodes": [
            {"id": "trigger-1", "type": "trigger", "name": "t",
             "config": {"triggerType": "webhook", "webhookUrl": "/hooks/refund"}},
            {"id": "msg-1", "type": "tool_call", "name": "通知",
             "config": {"tool": tool, "params": json.dumps(
                 {"channel": "email", "to": ["ops@example.com"], "subject": "s", "body": "ok"})}},
        ],
        "edges": [{"id": "e1", "source": "trigger-1", "target": "msg-1"}],
    }


GOOD_TOOL = "message/send"
BAD_TOOL = "nonexistent/tool"


def record_golden_case(cli: httpx.Client, graph_id: str, graph: dict, name: str) -> None:
    run = cli.post(f"/api/graphs/{graph_id}/run", json={"inputs": {}})
    ok(run.status_code == 200, f"录制基线运行 {run.status_code}")
    result = run.json()
    node_types = {node["id"]: node["type"] for node in graph["nodes"]}
    steps = [
        {"node_id": node_id, "node_type": node_types[node_id], "output": output}
        for node_id, output in result["outputs"].items()
    ]
    resp = cli.post("/api/recordings", json={
        "name": name, "graph_id": graph_id, "inputs": {}, "steps": steps, "status": result["status"],
    })
    ok(resp.status_code == 201, f"录为用例 {resp.status_code}")


def stage_graph_a(cli: httpx.Client) -> str:
    print("=== 图 A：manual / publish-gate 两触发、通过/blocked 两结论 ===")
    graph = message_graph(GOOD_TOOL)
    graph_id = cli.post("/api/graphs", json=graph).json()["id"]
    print(f"  graph_id={graph_id}")
    record_golden_case(cli, graph_id, graph, "D26 冒烟黄金用例")

    # rr-1 manual 通过
    r1 = cli.post(f"/api/graphs/{graph_id}/release-gate")
    ok(r1.status_code == 200 and r1.json().get("id") == "rr-1" and not r1.json()["blocked"],
       f"好草稿手动门禁通过并沉淀（id={r1.json().get('id')}）")

    # rr-2 manual blocked
    ok(cli.put(f"/api/graphs/{graph_id}", json=message_graph(BAD_TOOL)).status_code == 200,
       "PUT 坏草稿")
    r2 = cli.post(f"/api/graphs/{graph_id}/release-gate")
    body2 = r2.json()
    ok(body2.get("id") == "rr-2" and body2["blocked"]
       and body2["failed"] == body2["total"] and body2["total"] >= 1,
       f"坏草稿手动门禁 blocked（id={body2.get('id')} failed={body2['failed']}/{body2['total']}）")

    # rr-3 publish-gate blocked：409 体带 id、不产版本
    blocked_pub = cli.post(f"/api/graphs/{graph_id}/publish", json={"gate": True})
    body3 = blocked_pub.json()["detail"]["report"]
    ok(blocked_pub.status_code == 409 and body3.get("id") == "rr-3"
       and body3["trigger"] == "publish-gate" and body3["blocked"],
       f"带门禁发布坏草稿 → 409 且报告带 id（id={body3.get('id')}）")
    versions = cli.get(f"/api/graphs/{graph_id}/versions").json()["items"]
    ok(versions == [], f"blocked 不产版本（versions={versions}）")

    # rr-4 publish-gate 通过 → v1
    ok(cli.put(f"/api/graphs/{graph_id}", json=message_graph(GOOD_TOOL)).status_code == 200,
       "PUT 恢复好草稿")
    pub = cli.post(f"/api/graphs/{graph_id}/publish", json={"gate": True})
    body4 = pub.json()
    ok(pub.status_code == 200 and body4["releaseVersion"] == 1,
       f"修复后带门禁发布 v{body4.get('releaseVersion')}")

    # 列表：倒序、摘要无 cases
    listing = cli.get(f"/api/graphs/{graph_id}/release-reports")
    items = listing.json()["items"]
    ok(listing.status_code == 200 and [r["id"] for r in items] == ["rr-4", "rr-3", "rr-2", "rr-1"],
       f"列表倒序 4 条（ids={[r['id'] for r in items]}）")
    ok(all("cases" not in r for r in items), "摘要列表不含 cases")
    triggers = {r["id"]: r["trigger"] for r in items}
    ok(triggers["rr-1"] == "manual" and triggers["rr-3"] == "publish-gate",
       f"trigger 标注正确（{triggers['rr-1']}/{triggers['rr-3']}）")
    ok(items[0]["pass_rate"] == 1.0, f"通过报告 pass_rate=1.0（实际 {items[0]['pass_rate']}）")
    blocked_row = next(r for r in items if r["id"] == "rr-2")
    ok(blocked_row["pass_rate"] == 0.0 and blocked_row["blocked"] is True,
       f"blocked 报告 pass_rate=0.0（实际 {blocked_row['pass_rate']}）")

    # 详情：含 cases
    detail = cli.get(f"/api/graphs/{graph_id}/release-reports/rr-2")
    cases = detail.json()["cases"]
    ok(detail.status_code == 200 and cases and all(c["matches"] is False for c in cases),
       f"rr-2 详情含 {len(cases)} 条且全部不匹配")

    # 跨图取详情 → 404（用图 B 的 id 查图 A 的报告，在 stage_graph_b 后补验）
    return graph_id


def stage_graph_b(cli: httpx.Client) -> tuple[str, str]:
    print("=== 图 B：无录制用例 → skipped 未覆盖也沉淀（pass_rate=null）===")
    graph_id = cli.post("/api/graphs", json=message_graph(GOOD_TOOL)).json()["id"]
    print(f"  graph_id={graph_id}")
    report = cli.post(f"/api/graphs/{graph_id}/release-gate").json()
    # rr-N 为租户级单调计数（图 A 已用 rr-1..4），非每图重置
    rid = report.get("id")
    ok(rid == "rr-5" and report["total"] == 0 and report["skipped"] is True,
       f"无用例门禁 skipped（id={rid}）")
    items = cli.get(f"/api/graphs/{graph_id}/release-reports").json()["items"]
    ok(len(items) == 1 and items[0]["pass_rate"] is None,
       f"未覆盖报告 pass_rate=null（实际 {items[0]['pass_rate']}）")
    return graph_id, rid


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true",
                    help="跳过结尾 reset，保留造数供浏览器冒烟截图")
    args = ap.parse_args()
    with httpx.Client(base_url=BASE_API, timeout=30) as cli:
        # reset 需 administer 权限：先登录再 reset（否则 401 静默失败、计数不归零）
        login = cli.post("/api/auth/login", json={"username": "admin-a", "password": "admin123"})
        ok(login.status_code == 200, f"admin-a 登录 {login.status_code}")
        cli.headers["Authorization"] = f"Bearer {login.json()['token']}"
        reset_resp = cli.post("/api/demo/reset")
        ok(reset_resp.status_code == 200, f"租户 reset {reset_resp.status_code}")
        # 录制用例按产品语义跨 reset 保留；冒烟脚本需自隔离，逐个 DELETE 遗留用例
        legacy = cli.get("/api/recordings").json()["items"]
        for item in legacy:
            cli.delete(f"/api/recordings/{item['id']}")
        ok(cli.get("/api/recordings").json()["items"] == [], f"清空遗留录制用例 {len(legacy)} 条")

        graph_a = stage_graph_a(cli)
        graph_b, rid_b = stage_graph_b(cli)

        print("=== 隔离与 reset 语义 ===")
        # 跨图取报告 → 404
        cross = cli.get(f"/api/graphs/{graph_b}/release-reports/{rid_b}")
        ok(cross.status_code == 200, f"图 B 自身 {rid_b} 可读")
        cross_miss = cli.get(f"/api/graphs/{graph_b}/release-reports/rr-2")
        ok(cross_miss.status_code == 404, f"图 B 无 rr-2 → 404（{cross_miss.status_code}）")
        missing_graph = cli.get("/api/graphs/ghost-graph/release-reports")
        ok(missing_graph.status_code == 404, f"不存在的图 → 404（{missing_graph.status_code}）")

        if args.keep:
            print(f"=== --keep：保留造数（图 A={graph_a} 图 B={graph_b}）供浏览器截图 ===")
            print("=== D26 报告 v1 API 冒烟通过（不含 reset 段）===")
            return 0

        # reset：图与报告清空、录制用例保留（报告 ring 清空另有后端单测 U60 ⑥）
        recordings_before = len(cli.get("/api/recordings").json()["items"])
        ok(recordings_before >= 1, f"reset 前有录制用例 {recordings_before} 条")
        cli.post("/api/demo/reset")
        reports_after = cli.get(f"/api/graphs/{graph_a}/release-reports")
        ok(reports_after.status_code == 404,
           f"reset 清图，历史端点随之 404（{reports_after.status_code}）")
        recordings_after = len(cli.get("/api/recordings").json()["items"])
        ok(recordings_after == recordings_before,
           f"reset 保留录制用例（{recordings_before}→{recordings_after}）")

    print("=== D26 报告 v1 API 冒烟通过（两触发沉淀 / 倒序历史 / 详情 / 隔离 / reset 语义）===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
