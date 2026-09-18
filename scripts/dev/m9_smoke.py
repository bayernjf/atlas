# -*- coding: utf-8 -*-
"""M9 入站 Router + 灰度发布 + 指标门控自动回滚 全链路冒烟（真实 :8000，admin-a）。

纯 HTTP 驱动（正好覆盖批 4 新增的 PUT 同图草稿端点），两张图各验一条主线：

图 A 发布门禁 + 三段分桶（docs/13 U57/U56、04 §5.11/§5.16）：
  建图 → 录 1 个黄金用例 → 门禁过 → 发 v1 → PUT 坏草稿门禁拦截（409、不产版本）
  → PUT 恢复好草稿发 v2 → 配 internal(不命中本租户)/低金额桶（无 canary/full 段）→ start
  → 小额事件落 candidate v2（低金额桶）、大额事件三段不命中落 stable v1。

图 B 门控越阈自动回滚（U58、04 §5.13 末业务门控、19 §2.3.3 金融硬条款）：
  好 v1 → 直接发布坏 v2（模拟门禁未覆盖/绕过的坏版本进 canary）
  → internal 全量 candidate + run_error_rate 阈值 0.02/最少样本 3
  → 3 次坏事件后自动 rolled_back（actor=auto）+ rollout_gate 告警带 action v2→v1
  → 此后新事件回落 stable v1（回滚只切新流量）。

在途实例 pin-to-version（canary 挂 human_approval→rollback→resume 帧冻结 candidate）
由 tests/test_api_rollout.py U56 覆盖，本脚本不重复。

用法：
  先起后端：.venv/bin/uvicorn atlas.api.main:app --port 8000
  再跑：    .venv/bin/python scripts/dev/m9_smoke.py [--api-only]
"""
from __future__ import annotations

import argparse
import json
import sys
import time

import httpx

BASE_API = "http://localhost:8000"


def ok(cond: bool, msg: str) -> None:
    print(f"  {'OK ' if cond else 'FAIL '} {msg}")
    if not cond:
        raise AssertionError(msg)


def message_graph(tool: str) -> dict:
    """trigger(webhook) → message 工具调用；params 全字面，好/坏只差 tool 是否注册。"""
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
    """跑一次草稿（无 event）取 outputs，绑 graph_id 录为黄金用例。"""
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


def event_run(cli: httpx.Client, graph_id: str, payload: dict) -> httpx.Response:
    return cli.post(
        f"/api/graphs/{graph_id}/run",
        json={"event": {"channel": "webhook", "payload": payload}, "inputs": {}},
    )


def latest_resolved(cli: httpx.Client, graph_id: str):
    runs = cli.get("/api/monitoring/runs", params={"graph_id": graph_id, "limit": 1}).json()["items"]
    return runs[0]["resolved_version"] if runs else None


def stage_gate_and_routing(cli: httpx.Client) -> None:
    print("=== 图 A：发布门禁 + 三段分桶 ===")
    graph = message_graph(GOOD_TOOL)
    graph_id = cli.post("/api/graphs", json=graph).json()["id"]
    print(f"  graph_id={graph_id}")

    record_golden_case(cli, graph_id, graph, "M9 冒烟黄金用例")

    report = cli.post(f"/api/graphs/{graph_id}/release-gate").json()
    ok(report["total"] >= 1 and report["failed"] == 0 and not report["blocked"],
       f"好草稿门禁通过（total={report['total']} failed={report['failed']}）")

    v1 = cli.post(f"/api/graphs/{graph_id}/publish", json={"gate": True}).json()["releaseVersion"]
    ok(v1 == 1, f"带门禁发布 v{v1}")

    # 坏草稿：门禁拦截，409 且不产新版本
    bad = cli.put(f"/api/graphs/{graph_id}", json=message_graph(BAD_TOOL))
    ok(bad.status_code == 200, f"PUT 覆盖坏草稿 {bad.status_code}")
    blocked_report = cli.post(f"/api/graphs/{graph_id}/release-gate").json()
    ok(blocked_report["blocked"] and blocked_report["failed"] >= 1,
       f"坏草稿门禁判定 blocked（failed={blocked_report['failed']}）")
    blocked_pub = cli.post(f"/api/graphs/{graph_id}/publish", json={"gate": True})
    ok(blocked_pub.status_code == 409 and blocked_pub.json()["detail"]["report"]["blocked"],
       f"带门禁发布坏草稿 → 409（{blocked_pub.status_code}）")
    versions_after_block = cli.get(f"/api/graphs/{graph_id}/versions").json()["items"]
    ok(versions_after_block == [1], f"拦截后不产版本（versions={versions_after_block}）")

    # 恢复好草稿 → v2
    restored = cli.put(f"/api/graphs/{graph_id}", json=message_graph(GOOD_TOOL))
    ok(restored.status_code == 200, "PUT 恢复好草稿")
    v2 = cli.post(f"/api/graphs/{graph_id}/publish", json={"gate": True}).json()["releaseVersion"]
    ok(v2 == 2, f"修复后发布 v{v2}")

    # 灰度配置：internal 不命中本租户、低金额桶 ≤200 全进；不放 canary/full 段，
    # 大额事件三段都不命中 → 落 stable（canary 百分比哈希由 U55/U56 单测覆盖）。
    config = {
        "strategy": "progressive",
        "rules": [
            {"to": "internal", "tenants": ["ghost-bank"]},
            {"to": "lowValueBucket", "field": "payload.amount", "op": "<=", "value": 200, "percent": 100},
        ],
        "gate": {"observeMinutes": 60, "autoRollback": False, "minSamples": 3, "metrics": []},
        "inFlightPolicy": "pin-to-version",
    }
    ok(cli.put(f"/api/graphs/{graph_id}/rollout", json=config).status_code == 200, "保存灰度配置")
    start = cli.post(f"/api/graphs/{graph_id}/rollout/start")
    ok(start.status_code == 200, f"启动 canary {start.status_code}")
    state = start.json()
    ok(state["stable"] == 1 and state["candidate"] == 2 and state["status"] == "canary",
       f"stable=v{state['stable']} candidate=v{state['candidate']} status={state['status']}")

    small = event_run(cli, graph_id, {"order_id": "12347", "amount": 128})
    ok(small.status_code == 200, f"小额事件运行 {small.status_code}")
    ok(latest_resolved(cli, graph_id) == 2, "小额（¥128 ≤ 200）落 candidate v2（低金额桶）")

    big = event_run(cli, graph_id, {"order_id": "12346", "amount": 5000})
    ok(big.status_code == 200, f"大额事件运行 {big.status_code}")
    ok(latest_resolved(cli, graph_id) == 1, "大额（¥5000、三段不命中）落 stable v1")

    traffic = cli.get(f"/api/graphs/{graph_id}/rollout").json()["traffic"]
    ok(traffic["candidate"] >= 1 and traffic["segments"]["lowValueBucket"] >= 1,
       f"流量计数 candidate={traffic['candidate']} 低金额桶={traffic['segments']['lowValueBucket']}")
    ok(traffic["stable"] >= 1, f"stable 计数={traffic['stable']}")


def stage_auto_rollback(cli: httpx.Client) -> None:
    print("=== 图 B：门控越阈自动回滚 + 告警动作 ===")
    graph_id = cli.post("/api/graphs", json=message_graph(GOOD_TOOL)).json()["id"]
    print(f"  graph_id={graph_id}")
    ok(cli.post(f"/api/graphs/{graph_id}/publish", json={}).json()["releaseVersion"] == 1,
       "发布好 v1")
    # 坏 v2：不带 gate 直接发布（模拟门禁未覆盖/绕过的坏版本进入 canary）
    ok(cli.put(f"/api/graphs/{graph_id}", json=message_graph(BAD_TOOL)).status_code == 200,
       "PUT 坏草稿")
    ok(cli.post(f"/api/graphs/{graph_id}/publish", json={}).json()["releaseVersion"] == 2,
       "不带门禁发布坏 v2")

    config = {
        "strategy": "progressive",
        "rules": [{"to": "internal", "tenants": ["t1"]}],
        "gate": {
            "observeMinutes": 60,
            "autoRollback": True,
            "minSamples": 3,
            "metrics": [{"id": "run_error_rate", "threshold": 0.02}],
        },
        "inFlightPolicy": "pin-to-version",
    }
    cli.put(f"/api/graphs/{graph_id}/rollout", json=config)
    start = cli.post(f"/api/graphs/{graph_id}/rollout/start")
    ok(start.status_code == 200 and start.json()["status"] == "canary", "启动 canary（internal 全量）")

    # 3 次坏事件（candidate v2 工具未注册 → 节点失败），第 3 次达最少样本后自动回滚
    for i in range(3):
        resp = event_run(cli, graph_id, {"order_id": f"900{i}", "amount": 100})
        ok(resp.status_code == 200, f"坏事件 {i + 1}/3 运行 {resp.status_code}")

    rolled_back = None
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        rolled_back = cli.get(f"/api/graphs/{graph_id}/rollout").json()
        if rolled_back["status"] == "rolled_back":
            break
        time.sleep(0.1)
    ok(rolled_back is not None and rolled_back["status"] == "rolled_back",
       f"越阈后状态 rolled_back（实际 {rolled_back and rolled_back['status']}）")
    ok(rolled_back["rollbackActor"] == "auto",
       f"回滚 actor=auto（实际 {rolled_back and rolled_back['rollbackActor']}）")

    alerts = cli.get("/api/alerts").json()["items"]
    gate_alert = next(
        (a for a in alerts if a.get("rule_id") == "rollout_gate" and a.get("graph_id") == graph_id),
        None,
    )
    ok(gate_alert is not None, "产生 rollout_gate 告警")
    action = (gate_alert or {}).get("action") or {}
    ok(action.get("type") == "rollback" and action.get("from_version") == 2
       and action.get("to_version") == 1 and action.get("actor") == "auto",
       f"告警动作 v{action.get('from_version')}→v{action.get('to_version')} actor={action.get('actor')}")

    # 回滚后新事件回落 stable v1（好工具，成功）
    after = event_run(cli, graph_id, {"order_id": "9099", "amount": 100})
    ok(after.status_code == 200 and after.json()["status"] == "completed",
       f"回滚后新事件成功 completed（{after.status_code}/{after.json().get('status')}）")
    ok(latest_resolved(cli, graph_id) == 1, "回滚后新事件落 stable v1（只切新流量）")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api-only", action="store_true", help="M9 冒烟仅 API（当前默认即仅 API）")
    args = ap.parse_args()  # noqa: F841（保留与 m8 一致的 --api-only 开关位）
    with httpx.Client(base_url=BASE_API, timeout=30) as cli:
        cli.post("/api/demo/reset")
        login = cli.post("/api/auth/login", json={"username": "admin-a", "password": "admin123"})
        ok(login.status_code == 200, f"admin-a 登录 {login.status_code}")
        cli.headers["Authorization"] = f"Bearer {login.json()['token']}"
        stage_gate_and_routing(cli)
        stage_auto_rollback(cli)
    print("=== M9 全链路冒烟通过（门禁拦截 / 三段分桶 / 门控自动回滚 + 告警动作）===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
