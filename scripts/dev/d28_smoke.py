# -*- coding: utf-8 -*-
"""D28 批 3 全链路冒烟（真实 :8000，admin-a，PG 档）。

纯 HTTP 驱动，为前端 Monitoring 页「适配器调用」Card 与「自定义规则」编辑器造数，
并断言后端契约（docs/28 §4.1 ⑧ / §4.2 ⑨、docs/13 U166 起）：

⑧ 适配器调用级埋点：
  - 真实工具（message/send）运行 → RunRecord.tool_calls 落 SUCCESS 样本；
  - metrics.tools 按工具聚合 calls/failed/simulated/error_codes/p50/p95；
  - 未注册工具运行 → FAILED 样本并带 error_code（run 失败）。
⑨ 自定义告警规则 DSL：
  - PUT /api/monitoring/rules 带 custom 段 → GET 回读往返（旧四段纯超集）；
  - 坏图 error 运行命中 custom:{cid} 规则并落告警（PG v1 rule_name 不持久化，回退 rule_id）。

用法：
  后端（PG 档）：ATLAS_STORAGE_BACKEND=pg DATABASE_URL=... \
      .venv/bin/uvicorn atlas.api.main:app --port 8000
  跑：          .venv/bin/python scripts/dev/d28_smoke.py
"""
from __future__ import annotations

import json

import httpx

BASE_API = "http://localhost:8000"


def ok(cond: bool, msg: str) -> None:
    print(f"  {'OK ' if cond else 'FAIL '} {msg}")
    if not cond:
        raise AssertionError(msg)


def tool_graph(tool: str) -> dict:
    return {
        "version": 1,
        "variables": [],
        "nodes": [
            {"id": "trigger-1", "type": "trigger", "name": "t",
             "config": {"triggerType": "webhook", "webhookUrl": "/hooks/d28"}},
            {"id": "tool-1", "type": "tool_call", "name": "工具",
             "config": {"tool": tool, "params": json.dumps(
                 {"channel": "email", "to": ["ops@example.com"], "subject": "d28", "body": "ok"})}},
        ],
        "edges": [{"id": "e1", "source": "trigger-1", "target": "tool-1"}],
    }


GOOD_TOOL = "message/send"
BAD_TOOL = "nonexistent/d28_tool"
CID = "d28-smoke-error"


def main() -> int:
    with httpx.Client(base_url=BASE_API, timeout=30) as cli:
        login = cli.post("/api/auth/login", json={"username": "admin-a", "password": "admin123"})
        ok(login.status_code == 200, f"admin-a 登录 {login.status_code}")
        cli.headers["Authorization"] = f"Bearer {login.json()['token']}"

        print("=== ⑧ 适配器调用埋点 ===")
        good = tool_graph(GOOD_TOOL)
        good_id = cli.post("/api/graphs", json=good).json()["id"]
        for i in range(3):
            r = cli.post(f"/api/graphs/{good_id}/run", json={"inputs": {}})
            ok(r.status_code == 200, f"好工具运行 #{i + 1} {r.status_code}")
        runs_good = cli.get(f"/api/monitoring/runs?graph_id={good_id}").json()["items"]
        tool_calls = [tc for run in runs_good for tc in (run.get("tool_calls") or [])]
        ok(len(tool_calls) >= 3, f"好工具 RunRecord.tool_calls 落库 {len(tool_calls)} 条（≥3）")
        if tool_calls:
            sample = tool_calls[0]
            ok(sample["tool"] == GOOD_TOOL, f"样本 tool={sample['tool']}")
            ok(sample["action_status"] == "SUCCESS", f"样本 action_status={sample['action_status']}")
            ok(isinstance(sample["duration_ms"], (int, float)) and sample["duration_ms"] >= 0,
               f"样本 duration_ms={sample['duration_ms']}")

        bad = tool_graph(BAD_TOOL)
        bad_id = cli.post("/api/graphs", json=bad).json()["id"]
        rb = cli.post(f"/api/graphs/{bad_id}/run", json={"inputs": {}})
        ok(rb.status_code == 200, f"坏工具运行返回 200（图执行态由结果表达）{rb.status_code}")
        runs_bad = cli.get(f"/api/monitoring/runs?graph_id={bad_id}").json()["items"]
        bad_calls = [tc for run in runs_bad for tc in (run.get("tool_calls") or [])]
        ok(any(tc["action_status"] == "FAILED" for tc in bad_calls),
           f"坏工具采集到 FAILED 样本（{len(bad_calls)} 条）")
        if bad_calls:
            failed = next(tc for tc in bad_calls if tc["action_status"] == "FAILED")
            # error_code 为可空：未注册工具只有错误信息、无结构化 code；有 code 时才进 error_codes 聚合
            ok("error_code" in failed, f"FAILED 样本含 error_code 字段（值={failed.get('error_code')!r}，可空）")

        metrics = cli.get("/api/monitoring/metrics").json()
        rows = {row["tool"]: row for row in (metrics.get("tools") or [])}
        ok(GOOD_TOOL in rows, "metrics.tools 含 message/send 聚合行")
        if GOOD_TOOL in rows:
            row = rows[GOOD_TOOL]
            ok(row["calls"] >= 3 and row["failed"] == 0,
               f"message/send calls={row['calls']} failed={row['failed']}")
            ok(row["p50"] is not None and row["p95"] is not None,
               f"真实样本≥3，p50={row['p50']} p95={row['p95']}（SIMULATED 不纳分位）")
            print(f"    聚合：{row}")
        if BAD_TOOL in rows:
            brow = rows[BAD_TOOL]
            ok(brow["failed"] >= 1,
               f"坏工具聚合 failed={brow['failed']} error_codes={brow['error_codes']}（无结构化 code 时为空 map）")

        print("=== ⑨ 自定义规则 DSL ===")
        rules_payload = {
            "run_error": {"enabled": True},
            "node_failed": {"enabled": True},
            "consecutive_failures": {"enabled": True, "threshold": 3},
            "failure_rate": {"enabled": True, "window": 20, "min_samples": 5, "rate": 0.5},
            "custom": [
                {"cid": CID, "name": "D28 冒烟错误即告警", "enabled": True,
                 "expression": "{{status}} == 'error' || {{hasError}}", "severity": "critical"},
                {"cid": "d28-smoke-slow", "name": "D28 冒烟慢运行", "enabled": False,
                 "expression": "{{durationMs}} > 500", "severity": "warning"},
            ],
        }
        put = cli.put("/api/monitoring/rules", json=rules_payload)
        ok(put.status_code == 200, f"PUT 规则含 custom {put.status_code} {put.text if put.status_code != 200 else ''}")
        got = cli.get("/api/monitoring/rules").json()
        cids = {c["cid"]: c for c in (got.get("custom") or [])}
        ok(CID in cids, "GET 规则回读含 custom 段（纯超集往返）")
        ok(cids.get(CID, {}).get("severity") == "critical", "回读 severity=critical")

        # 非法表达式应 422（静态校验，禁 eval 引擎）
        bad_put = cli.put("/api/monitoring/rules", json={
            **{k: v for k, v in rules_payload.items() if k != "custom"},
            "custom": [{"cid": "x", "name": "坏", "expression": "1 +", "severity": "warning"}],
        })
        ok(bad_put.status_code == 422, f"非法自定义表达式 422（实际 {bad_put.status_code}）")

        # 再跑一次坏图触发 custom 规则
        cli.post(f"/api/graphs/{bad_id}/run", json={"inputs": {}})
        alerts = cli.get("/api/alerts").json()["items"]
        custom_alerts = [a for a in alerts if a["rule_id"] == f"custom:{CID}"]
        ok(len(custom_alerts) >= 1, f"坏运行命中 custom:{CID} 并落告警（{len(custom_alerts)} 条）")
        if custom_alerts:
            ca = custom_alerts[0]
            ok(ca["severity"] == "critical", f"自定义告警 severity={ca['severity']}")
            print(f"    自定义告警：rule_id={ca['rule_id']} rule_name={ca.get('rule_name')!r} "
                  f"count={ca['count']}（PG v1 rule_name 不持久化则为 null，前端回退 rule_id）")

        print("=== D28 批 3 冒烟全部通过 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
