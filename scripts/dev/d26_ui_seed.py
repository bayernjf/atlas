# -*- coding: utf-8 -*-
"""D26 浏览器冒烟造数：对指定已存图补造完整报告历史（manual/publish-gate × 通过/blocked）。

供 UI 冒烟使用（图由浏览器"从模板新建 → 发布"保存得到）：
  录 1 条 rejected 黄金用例 → 手动门禁通过（manual）
  → PUT 篡改草稿（拒绝消息 body 改字）→ 手动门禁 blocked（manual）
  → 带门禁发布 409（publish-gate blocked，不产版本）
  → PUT 恢复模板原草稿 → 带门禁发布 v1（publish-gate 通过）。

用法：.venv/bin/python scripts/dev/d26_ui_seed.py <graph_id>
"""
from __future__ import annotations

import copy
import json
import sys

import httpx

BASE_API = "http://localhost:8000"
TEMPLATE_ID = "approval-timeout-reject"


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    graph_id = sys.argv[1]
    with httpx.Client(base_url=BASE_API, timeout=30) as cli:
        login = cli.post("/api/auth/login", json={"username": "admin-a", "password": "admin123"})
        login.raise_for_status()
        cli.headers["Authorization"] = f"Bearer {login.json()['token']}"

        tpl = cli.get(f"/api/templates/{TEMPLATE_ID}").json()["graph"]

        # 1) 真跑 rejected 终态录为黄金用例（inputs.approvals 模拟审批人拒绝）
        run = cli.post(
            f"/api/graphs/{graph_id}/run",
            json={"inputs": {"approvals": {"approval-1": "rejected"}}},
        )
        assert run.status_code == 200, run.text
        result = run.json()
        node_types = {n["id"]: n["type"] for n in tpl["nodes"]}
        steps = [
            {"node_id": nid, "node_type": node_types.get(nid, "unknown"), "output": out}
            for nid, out in result["outputs"].items()
        ]
        rec = cli.post("/api/recordings", json={
            "name": "D26 UI 冒烟·审批拒绝黄金用例",
            "graph_id": graph_id,
            "inputs": {"approvals": {"approval-1": "rejected"}},
            "steps": steps,
            "status": result["status"],
        })
        assert rec.status_code == 201, rec.text
        print(f"OK 录制用例 {rec.json()['id']}（基线 status={result['status']}）")

        # 2) 手动门禁通过（确保草稿与模板一致）
        cli.put(f"/api/graphs/{graph_id}", json=tpl)
        r_pass = cli.post(f"/api/graphs/{graph_id}/release-gate").json()
        assert not r_pass["blocked"] and r_pass["total"] >= 1, r_pass
        print(f"OK {r_pass['id']} manual 通过 {r_pass['passed']}/{r_pass['total']}")

        # 3) 篡改：拒绝消息节点 body 改字 → 产出不一致
        bad = copy.deepcopy(tpl)
        tampered = False
        for node in bad["nodes"]:
            cfg = node.get("config") or {}
            params = cfg.get("params")
            if node["type"] == "tool_call" and isinstance(params, str) and "拒绝" in params:
                payload = json.loads(params)
                if isinstance(payload.get("body"), str):
                    payload["body"] = payload["body"] + "【篡改】"
                    cfg["params"] = json.dumps(payload, ensure_ascii=False)
                    tampered = True
                    print(f"OK 篡改节点 {node['id']} 的消息 body")
                    break
        assert tampered, "未找到可篡改的拒绝消息节点"
        assert cli.put(f"/api/graphs/{graph_id}", json=bad).status_code == 200

        r_bad = cli.post(f"/api/graphs/{graph_id}/release-gate").json()
        assert r_bad["blocked"] and r_bad["failed"] >= 1, r_bad
        print(f"OK {r_bad['id']} manual blocked {r_bad['failed']}/{r_bad['total']}")

        # 4) 带门禁发布坏草稿 → 409
        blocked_pub = cli.post(f"/api/graphs/{graph_id}/publish", json={"gate": True})
        assert blocked_pub.status_code == 409, blocked_pub.text
        rid = blocked_pub.json()["detail"]["report"]["id"]
        versions_after = cli.get(f"/api/graphs/{graph_id}/versions").json()["items"]
        print(f"OK {rid} publish-gate blocked 409，versions={versions_after}")

        # 5) 恢复好草稿 → 带门禁发布 v1
        assert cli.put(f"/api/graphs/{graph_id}", json=tpl).status_code == 200
        pub = cli.post(f"/api/graphs/{graph_id}/publish", json={"gate": True})
        assert pub.status_code == 200, pub.text
        print(f"OK {pub.json()['releaseVersion']} 发布成功（publish-gate 通过）")

        items = cli.get(f"/api/graphs/{graph_id}/release-reports").json()["items"]
        print("历史（倒序）：")
        for r in items:
            print(
                f"  {r['id']} {r['trigger']:>12} "
                f"passed={r['passed']}/{r['total']} rate={r['pass_rate']} "
                f"blocked={r['blocked']} skipped={r['skipped']} at={r['created_at'][:16]}"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
