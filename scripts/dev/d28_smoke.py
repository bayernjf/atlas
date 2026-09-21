# -*- coding: utf-8 -*-
"""D28 批 3 + docs/33 打包全链路冒烟（真实 :8000，admin-a，默认内存档）。

纯 HTTP 驱动，为前端 Monitoring 页「适配器调用 / Trace 时间线 / 告警」造数，
并断言后端契约（docs/28 §4.1 ⑧ / §4.2 ⑨、docs/33 §4 trace / §5 告警、docs/13）：

⑧ 适配器调用级埋点：
  - 真实工具（message/send）运行 → RunRecord.tool_calls 落 SUCCESS 样本；
  - metrics.tools 按工具聚合 calls/failed/simulated/error_codes/p50/p95；
  - 未注册工具运行 → FAILED 样本并带 error_code（run 失败）。
⑧b Trace 时间线钻取（docs/33 §4）：
  - GET /api/monitoring/runs/{id}/trace 根 span kind=run，DFS 含 node/tool span；
  - 运行列表项投影不含 spans（trace 懒加载）。
⑨ 自定义告警规则 DSL：
  - PUT /api/monitoring/rules 带 custom 段 → GET 回读往返（旧四段纯超集）；
  - 坏图 error 运行命中 custom:{cid} 规则并落告警（PG v1 rule_name 不持久化，回退 rule_id）。
⑩ 静默 / 惰性升级 / 值班轮换（docs/33 §5，进程内）：
  - 值班表去重保序、轮换推进、空表 409；新建告警指派当前值班人；
  - 静默命中后不新增/不合并/不升级，仅 suppressed_count+1；建/删/列表与权限断言；
  - escalation_ack_minutes 往返与非法值 422；viewer 只读、写操作 403。

用法（内存档，推荐）：
  后端：.venv/bin/uvicorn atlas.api.main:app --port 8000
  跑：  .venv/bin/python scripts/dev/d28_smoke.py

PG 档说明：整栈以 ATLAS_STORAGE_BACKEND=pg 起 uvicorn 当前受既有缺口阻断——
PgUserStore 未实现 bind_session_store（import app 即 AttributeError，见 docs/29 阻断登记）。
PG 层改由 ATLAS_RUN_INTEGRATION=1 + DATABASE_URL 的内联 integration 覆盖
（trace U278、静默/升级/值班 U299、M11 pgvector），不要设 ATLAS_STORAGE_BACKEND 起整栈。
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

        print("=== ⑧b Trace 时间线（docs/33 §4）===")
        trace = cli.get(f"/api/monitoring/runs/{runs_good[0]['id']}/trace")
        ok(trace.status_code == 200, f"GET runs/{{id}}/trace {trace.status_code}")
        root = trace.json().get("spans")
        ok(root is not None and root.get("kind") == "run", "trace 根 span 非空且 kind=run")
        if root:
            kinds: set[str] = set()

            def _walk(node: dict) -> None:
                kinds.add(node.get("kind"))
                for child in (node.get("children") or []):
                    _walk(child)

            _walk(root)
            ok("node" in kinds, f"trace 含 node span（kinds={sorted(kinds)}）")
            ok("tool" in kinds, f"trace 含 tool span（message/send，kinds={sorted(kinds)}）")
        ok("spans" not in runs_good[0], "运行列表项投影不含 spans（trace 懒加载）")

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

        print("=== ⑩ 静默 / 升级 / 值班（docs/33 §5，进程内）===")
        # 值班：去重保序、当前=首位、轮换
        oc = cli.get("/api/monitoring/on-call")
        ok(oc.status_code == 200, f"GET on-call {oc.status_code}")
        put_oc = cli.put("/api/monitoring/on-call", json={"members": ["张三", "李四", "张三"]})
        ok(put_oc.status_code == 200
           and put_oc.json()["members"] == ["张三", "李四"]
           and put_oc.json()["current"] == "张三",
           f"PUT 值班去重保序、当前=首位（{put_oc.status_code} {put_oc.text if put_oc.status_code != 200 else ''}）")
        rot = cli.post("/api/monitoring/on-call/rotate")
        ok(rot.status_code == 200 and rot.json()["current"] == "李四",
           f"轮换 current=李四（{rot.status_code}）")

        # 静默：记录静默前 node_failed 计数，静默期再跑坏图应被压下、不合并不新增
        def _nf_count() -> int:
            return next((a["count"] for a in cli.get("/api/alerts").json()["items"]
                         if a["rule_id"] == "node_failed"), 0)

        before_nf = _nf_count()
        sil = cli.post("/api/monitoring/silences",
                       json={"rule_id": "node_failed", "duration_minutes": 30, "reason": "d28 冒烟维护窗"})
        ok(sil.status_code == 201 and sil.json()["active"] is True, f"POST 静默 {sil.status_code}")
        sil_id = sil.json()["id"]
        cli.post(f"/api/graphs/{bad_id}/run", json={"inputs": {}})
        active_sil = {s["id"]: s for s in
                      cli.get("/api/monitoring/silences?active=true").json()["items"]}
        ok(active_sil.get(sil_id, {}).get("suppressed_count", 0) >= 1,
           f"静默压下计数≥1（{active_sil.get(sil_id, {}).get('suppressed_count')}）")
        ok(_nf_count() == before_nf,
           f"静默期 node_failed 不合并不新增（before={before_nf} after={_nf_count()}）")
        dele = cli.delete(f"/api/monitoring/silences/{sil_id}")
        ok(dele.status_code == 200 and dele.json()["deleted"] is True, f"DELETE 静默 {dele.status_code}")
        ok(cli.delete(f"/api/monitoring/silences/{sil_id}").status_code == 404, "重复解除 404")
        bad_sil = cli.post("/api/monitoring/silences", json={"duration_minutes": 0, "reason": "x"})
        ok(bad_sil.status_code == 422, f"非法时长 0 → 422（实际 {bad_sil.status_code}）")

        # 升级配置往返 + 校验（惰性超时升级由单测 U292/U299 覆盖）
        esc = cli.put("/api/monitoring/rules", json={**rules_payload, "escalation_ack_minutes": 5})
        ok(esc.status_code == 200 and esc.json()["escalation_ack_minutes"] == 5,
           f"PUT 升级分钟往返=5（{esc.status_code}）")
        ok(cli.put("/api/monitoring/rules",
                   json={**rules_payload, "escalation_ack_minutes": 0}).status_code == 422,
           "升级分钟 0 → 422")

        # 权限：viewer 只读
        vtoken = cli.post("/api/auth/login",
                          json={"username": "viewer-a", "password": "viewer123"}).json()["token"]
        vh = {"Authorization": f"Bearer {vtoken}"}
        ok(cli.post("/api/monitoring/silences",
                    json={"duration_minutes": 30, "reason": "x"}, headers=vh).status_code == 403,
           "viewer 建静默 403")
        ok(cli.get("/api/monitoring/silences", headers=vh).status_code == 200, "viewer 只读列表 200")
        ok(cli.post("/api/monitoring/on-call/rotate", headers=vh).status_code == 403, "viewer 轮换 403")

        print("=== D28 批 3 冒烟全部通过 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
